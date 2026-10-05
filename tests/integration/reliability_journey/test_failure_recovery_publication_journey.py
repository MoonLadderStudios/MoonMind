"""Failure -> cumulative repair -> exact verification -> publication (#3512).

Real workflow controllers, SQL/artifact owners, checkpoint capture/cold restore,
host input materialization and saved-work publication are composed here. Provider
compute and Temporal transport are controlled; this is not live qualification or
the broader product.cumulative-remediation conformance matrix.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.exceptions import ApplicationError

from api_service.db import models
from api_service.services.checkpoint_branch_service import CheckpointBranchService
from moonmind.config.settings import settings
from moonmind.omnigent.execute import _build_omnigent_first_message, _first_message_text
from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer
from moonmind.omnigent.realizers.generic_host import GenericOmnigentHostRealizer
from moonmind.schemas.temporal_models import (
    STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE,
    StepExecutionIdentityModel,
)
from moonmind.workflows.executions.runtime_capabilities import (
    resolve_runtime_execution_capabilities,
)
from moonmind.workflows.temporal.artifacts import (
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.recovery_manifest import (
    build_failed_run_recovery_manifest,
)
from moonmind.workflows.temporal.remediation_actions import (
    RemediationActionAuthorityService,
    RemediationMutationGuardPolicy,
    RemediationMutationGuardService,
)
from moonmind.workflows.temporal.remediation_context import RemediationContextBuilder
from moonmind.workflows.temporal.remediation_tools import RemediationEvidenceToolService
from moonmind.workflows.temporal.remediation_workspace_head import (
    RemediationAttemptInput,
    RemediationAttemptOutput,
    RemediationHeadError,
    VerificationEvidence,
)
from moonmind.workflows.temporal.runtime.checkpoint_restore import (
    ManagedCheckpointRestoreService,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from moonmind.workflows.temporal.service import TemporalExecutionService
from moonmind.workflows.temporal.step_checkpoints import build_step_checkpoint_payload
from moonmind.workflows.temporal.workflows import run as run_module
from moonmind.workflows.temporal.workflows.checkpoint_branch_turn import (
    build_branch_turn_verification_handoff,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.support.saved_work_capture import capture_saved_work, git
from tests.unit.publish.test_saved_work_publication_journey import (
    OPERATOR,
    _persisted_result,
    _tree,
    journey,
)
from tests.unit.workflows.temporal.test_remediation_context import (
    _admin_permissions,
    _admin_profile,
    _create_target_and_remediation,
    _fast_verification_phase,
    _read_artifact_json,
)
from tests.unit.workflows.temporal.test_remediation_issue_3622 import AcceptedEffect
from tests.unit.workflows.temporal.workflows.test_run_recover_from_failed_step import (
    _configure_workflow_runtime,
    _dependency_map,
    _journey_remediation_spec,
    _ordered_nodes,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.integration_ci,
    pytest.mark.reliability_journey,
]


@pytest.mark.parametrize("corrected", [False, True], ids=["recovery", "corrected"])
async def test_failed_middle_step_retains_bytes_and_resumes_only_unfinished_phases(
    tmp_path, monkeypatch, corrected
):
    _configure_workflow_runtime(monkeypatch)
    monkeypatch.setattr(settings.workflow, "temporal_artifact_backend", "local_fs")
    monkeypatch.setattr(
        settings.workflow, "temporal_artifact_root", str(tmp_path / "artifacts")
    )
    # The supported credential-free local mode exercises input projection;
    # destination publication below uses the authenticated operator contract.
    monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "disabled")
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    now = datetime.now(UTC)
    monkeypatch.setattr(run_module.workflow, "now", lambda: now)
    # Replay-sensitive request additions use the same compiler patches as the
    # existing production input-materialization tests. Retained histories are
    # tested separately below.
    enabled = {
        run_module.RUN_REMEDIATION_EXPLICIT_EVIDENCE_INPUTS_PATCH,
        run_module.RUN_REMEDIATION_ATTEMPT_CONTEXT_INPUTS_PATCH,
        "run-remediation-current-evidence-inputs-v1",
        "run-materialize-evidence-retry-v1",
    }
    monkeypatch.setattr(run_module.workflow, "patched", lambda patch: patch in enabled)
    counts = {"prepare": 0, "failed_compute": 0, "repair": 0, "save": 0, "restore": 0}
    evidence = {}

    async def compute_and_save(service):
        session = service._repository._session
        client = MagicMock()
        client.start_workflow = AsyncMock()
        target, remediation = await _create_target_and_remediation(
            session, client, owner_id=OPERATOR, authority_mode="admin_auto"
        )
        target = await session.get(
            models.TemporalExecutionCanonicalRecord, target.workflow_id
        )
        run_module.workflow.info().workflow_id = target.workflow_id

        async def put(payload, content_type="application/json"):
            body = json.dumps(payload).encode()
            principal = (
                "system"
                if content_type == STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE
                else "default-user"
            )
            artifact, _ = await service.create(
                principal=principal,
                content_type=content_type,
                link={
                    "namespace": "moonmind",
                    "workflow_id": target.workflow_id,
                    "run_id": target.run_id,
                    "link_type": "output.primary",
                },
            )
            await service.write_complete(
                artifact_id=artifact.artifact_id, principal=principal, payload=body
            )
            return f"artifact://{artifact.artifact_id}"

        intent = {
            "instructions": "Keep the useful predecessor and add A, then B.",
            "runtime": {
                "mode": "omnigent",
                "executionProfileRef": "profile-3512",
                "model": "same-model",
                "effort": "high",
            },
            "publishMode": "pr",
            "repository": "MoonLadderStudios/MoonMind",
            "privacy": "private",
            "costBudget": 2,
        }
        objective_ref = await put(intent)
        stale_gate = await put({"remainingWork": ["obsolete work"]})
        stale_remaining = await put(["obsolete work"])
        parameters = {
            "workflow": intent,
            "repository": intent["repository"],
            "publishMode": "pr",
        }
        target.parameters = copy.deepcopy(parameters)
        source = MoonMindRunWorkflow()
        source._initialize_step_ledger(
            ordered_nodes=_ordered_nodes(),
            dependency_map=_dependency_map(),
            updated_at=now,
        )

        def prepare(repo):
            counts["prepare"] += 1
            (repo / "predecessor.txt").write_bytes(b"useful predecessor\n")
            (repo / "candidate.txt").write_bytes(b"C0\n")

        async def capture(
            ordinal, mutate, workflow_id, run_id, logical_step_id="implement"
        ):
            counts["save"] += 1
            saved = await capture_saved_work(
                tmp_path / f"C{ordinal}",
                {"base.txt": "original repository\n"},
                mutate,
                artifact_service=service,
                workflow_id=workflow_id,
                run_id=run_id,
                logical_step_id=logical_step_id,
            )
            checkpoint = build_step_checkpoint_payload(
                identity=StepExecutionIdentityModel(
                    workflowId=workflow_id,
                    runId=run_id,
                    logicalStepId=logical_step_id,
                    executionOrdinal=1,
                ),
                boundary="after_execution",
                task_input_snapshot_ref=objective_ref,
                workspace=saved.workspace_evidence,
                created_at=now,
                plan_digest="sha256:3512",
            )
            ref = await put(checkpoint, STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE)
            return saved, ref, checkpoint

        saved, root_ref, root_checkpoint = await capture(
            0, prepare, target.workflow_id, target.run_id
        )
        source._mark_step_running(
            "prepare", updated_at=now, summary="Produce useful output"
        )
        source._record_step_result_evidence(
            "prepare",
            execution_result={
                "outputs": {
                    "outputPrimaryRef": saved.saved_work_ref,
                    "stateCheckpointRef": root_ref,
                }
            },
            updated_at=now,
        )
        source._step_ledger_row_for("prepare")["status"] = "succeeded"
        source._mark_step_running("implement", updated_at=now, summary="Later step")
        counts["failed_compute"] += 1
        diagnostic = source._record_step_execution_exception(
            RuntimeError("controlled later-step failure"),
            logical_step_id="implement",
            tool_name="omnigent",
            source="activity",
            updated_at=now,
        )
        target.state = models.MoonMindWorkflowState.FAILED
        target.close_status = models.TemporalExecutionCloseStatus.FAILED
        target.memo = {
            **target.memo,
            "failureDiagnostic": diagnostic,
            "task_input_snapshot_ref": objective_ref,
            "recovery_checkpoint_ref": root_ref,
        }
        await session.commit()
        source_snapshot = copy.deepcopy(
            (target.parameters, target.memo, source._step_ledger_rows)
        )
        saved.remove_source()  # the failed host cannot supply recovery content

        capabilities = resolve_runtime_execution_capabilities("omnigent")
        manifest = build_failed_run_recovery_manifest(
            workflow_id=target.workflow_id,
            run_id=target.run_id,
            created_at=now,
            step_ledger_rows=source._step_ledger_rows,
            failure_diagnostic=diagnostic,
            terminal_dispositions={"prepare": "accepted"},
            checkpoint_refs_by_boundary={"implement": {"after_execution": root_ref}},
            checkpoint_kind=root_checkpoint["workspace"]["kind"],
            runtime_capabilities=capabilities,
            restore_route_registered=True,
        ).model_dump(by_alias=True, mode="json")
        assert manifest["resumeAllowed"] is True
        client.start_workflow.side_effect = None
        client.start_workflow.return_value = SimpleNamespace(run_id="recovery-run")
        recovered = await TemporalExecutionService(
            session, client_adapter=client
        ).create_failed_step_recovery_execution(
            target,
            recovery_checkpoint_ref=root_ref,
            idempotency_key="3512-recovery",
            failed_run_recovery_manifest_ref=await put(manifest),
            failed_run_recovery_manifest=manifest,
            checkpoint_payload={
                "source": {"workflowId": target.workflow_id, "runId": target.run_id},
                "taskInputSnapshotRef": objective_ref,
                "planDigest": "sha256:3512",
                "failedStep": {
                    "logicalStepId": "implement",
                    "order": 2,
                    "executionOrdinal": 1,
                },
                "recoveryWorkspace": {"checkpointRef": root_ref},
                "preservedSteps": [
                    {
                        "logicalStepId": "prepare",
                        "order": 1,
                        "status": "completed",
                        "sourceExecutionOrdinal": 1,
                        "artifacts": {"outputPrimary": saved.saved_work_ref},
                        "stateCheckpointRef": root_ref,
                    }
                ],
            },
        )
        recovery_record = await session.get(
            models.TemporalExecutionCanonicalRecord,
            recovered["execution"]["workflowId"],
        )
        for key, value in intent.items():
            assert recovery_record.parameters["workflow"][key] == value
        recovery_source = recovery_record.parameters["recoverySource"]
        workflow = MoonMindRunWorkflow()
        workflow._recovery_source = recovery_source
        workflow._initialize_step_ledger(
            ordered_nodes=_ordered_nodes(),
            dependency_map=_dependency_map(),
            updated_at=now,
        )
        assert workflow._step_ledger_row_for("prepare")["executionOrdinal"] == 0
        assert (
            workflow._restore_recovery_workspace_for_failed_step("implement")
            == root_ref
        )
        spec = _journey_remediation_spec()
        tool_inputs = dict(spec.remediation_tool.inputs)
        instructions = (
            "Correct the failed step: add A, then B."
            if corrected
            else intent["instructions"]
        )
        instruction_payload = {**intent, "instructions": instructions}
        instruction_ref = await put(instruction_payload) if corrected else objective_ref
        tool_inputs.update(
            inputRefs=list(dict.fromkeys([objective_ref, instruction_ref])),
            instructions=instructions,
        )
        spec = spec.model_copy(
            update={
                "remediation_tool": spec.remediation_tool.model_copy(
                    update={"inputs": tool_inputs}
                )
            }
        )
        controller = {
            "id": "initial-verification",
            "annotations": {"remediationLoop": spec.model_dump(by_alias=True)},
            "inputs": {
                "runtime": {
                    **intent["runtime"],
                    "gateResultRef": stale_gate,
                    "remainingWorkRef": stale_remaining,
                }
            },
        }
        workflow._initialize_remediation_loop_controller(
            ordered_nodes=[controller], require_agent_instructions=False
        )
        workflow._write_json_artifact = lambda *, name, payload: put(payload)
        branch_owner = CheckpointBranchService(session)
        branch = await branch_owner.create_branch(
            {
                "branchId": "journey-3512",
                "label": "Recover the useful candidate",
                "source": {
                    "workflowId": target.workflow_id,
                    "runId": target.run_id,
                    "checkpointBoundary": "after_execution",
                    "checkpointRef": root_ref,
                    "checkpointDigest": saved.workspace_evidence["workspaceDigest"],
                },
                "workspacePolicy": "continue_from_previous_execution",
                "runtimeContextPolicy": "fresh_agent_run",
            }
        )
        head = await branch_owner.initialize_remediation_head(
            workflow_id=target.workflow_id,
            branch_id=branch.branch_id,
            loop_id=spec.loop_id,
        )
        turn = await branch_owner.create_turn(
            {
                "branchTurnId": "journey-3512-turn",
                "branchId": branch.branch_id,
                "sourceCheckpointRef": root_ref,
                "instructionRef": instruction_ref,
                "instructionDigest": "sha256:"
                + hashlib.sha256(json.dumps(instruction_payload).encode()).hexdigest(),
                "workspacePolicy": "continue_from_previous_execution",
                "runtimeContextPolicy": "fresh_agent_run",
                "idempotencyKey": "3512-turn",
            }
        )
        assert turn.instruction_ref == instruction_ref
        workflow._remediation_workspace_head = head
        previous_ref, previous_checkpoint = root_ref, root_checkpoint
        nodes = []
        gate = await put(
            {"verdict": "ADDITIONAL_WORK_NEEDED", "remainingWork": ["A", "B"]}
        )
        remaining = await put(["A", "B"])
        await workflow._evaluate_dynamic_remediation_verification(
            ordered_nodes=nodes,
            verdict="ADDITIONAL_WORK_NEEDED",
            gate_result_ref=gate,
            remaining_work_ref=remaining,
            workspace_head=head.model_dump(by_alias=True),
        )

        for ordinal in (1, 2):
            repair, verifier = nodes[-2:]
            inputs = dict(repair["inputs"])
            workflow._inject_remediation_workspace_baseline(
                node=repair, node_inputs=inputs
            )
            frozen = RemediationAttemptInput.model_validate(
                inputs["remediationAttemptInput"]
            )
            assert frozen.base_checkpoint_ref == (
                root_ref if ordinal == 1 else previous_ref
            )
            request = workflow._build_agent_execution_request(
                node_inputs=inputs,
                node_id=repair["id"],
                node_annotations=repair.get("annotations"),
                tool_name="omnigent",
                workflow_parameters=parameters,
                step_execution=1,
            )
            step_id = request.step_execution.step_execution_id
            workspace_id = hashlib.sha256(
                f"{request.correlation_id}:{step_id}".encode()
            ).hexdigest()[:24]
            authority = tmp_path / f"host-{ordinal}"
            SandboxWorkspaceRecordStore(authority).ensure(
                SandboxWorkspaceRecord(
                    workspace_id, request.correlation_id, step_id, "repo"
                )
            )
            locator = {
                "kind": "sandbox",
                "workspaceId": workspace_id,
                "relativePath": "repo",
            }
            request = request.model_copy(
                update={
                    "workspace_spec": {
                        "workspaceLocator": locator,
                        "repository": intent["repository"],
                    }
                }
            )
            restored = await ManagedCheckpointRestoreService(
                authority_root=authority,
                artifact_service=service,
                repository_source_root=tmp_path / "absent-source",
            ).restore(
                {
                    "schemaVersion": "v1",
                    "recoveryIdentity": {
                        "workflowId": request.correlation_id,
                        "runId": "run-recover",
                        "logicalStepId": repair["id"],
                        "executionOrdinal": ordinal,
                    },
                    "source": {
                        **previous_checkpoint["source"],
                        "checkpointRef": frozen.base_checkpoint_ref,
                        "checkpointBoundary": "after_execution",
                    },
                    "checkpoint": {
                        key: previous_checkpoint["workspace"][key]
                        for key in (
                            "kind",
                            "baseCommit",
                            "archiveRef",
                            "archiveDigest",
                            "manifestRef",
                            "manifestDigest",
                        )
                    },
                    "destination": {
                        **locator,
                        "stepExecutionId": step_id,
                        "repository": intent["repository"],
                    },
                    "workspacePolicy": "restore_pre_execution",
                    "resumePhase": "rerun_failed_step",
                    "capabilitySetVersion": capabilities.capability_set_version,
                    "capabilityDigest": capabilities.capability_digest,
                    "idempotencyKey": f"restore-{ordinal}",
                }
            )
            counts["restore"] += 1
            assert restored["restorationEvidenceRef"]
            workspace = authority / "temporal_sandbox" / workspace_id / "repo"
            materializer = OmnigentWorkspaceMaterializer(
                command_runner=AsyncMock(side_effect=AssertionError("no fresh clone")),
                workspace_root=authority,
                artifact_service=service,
            )
            attachment = await materializer.materialize(
                request, runtime_uid=os.getuid(), runtime_gid=os.getgid()
            )
            paths = attachment["materializedInputPaths"]
            # This is the consumer assertion: runtime defaults must never
            # replace the latest verifier report/remaining work from the loop.
            assert json.loads((workspace / paths["gateResultPath"]).read_text())[
                "remainingWork"
            ] == (["A", "B"] if ordinal == 1 else ["B"])
            assert json.loads((workspace / paths["remainingWorkPath"]).read_text()) == (
                ["A", "B"] if ordinal == 1 else ["B"]
            )
            assert any(
                json.loads((workspace / path).read_text()) == intent
                for path in paths.values()
            )
            assert any(
                json.loads((workspace / path).read_text()) == instruction_payload
                for path in paths.values()
            )
            plan = SimpleNamespace(
                planRef="plan-3512",
                payload=SimpleNamespace(
                    agentSource={"upstreamId": "agent-3512"},
                    endpointRef="same-endpoint",
                    harnessId="codex-native",
                    hostClassRef="same-host",
                    launchPolicyRef="same-launch",
                    capturePolicy={},
                    modelConfig=SimpleNamespace(
                        qualifiedId="same-model", effort="high"
                    ),
                ),
            )
            bound = object.__new__(GenericOmnigentHostRealizer)._bind_exact_host(
                request,
                plan,
                {
                    "omnigentHostId": "host-3512",
                    "workspacePath": str(workspace),
                    "materializedInputPaths": paths,
                },
                SimpleNamespace(
                    bindingId="binding-3512",
                    providerLeases={},
                    hostBindingRef="host-3512",
                    hostLeaseRef="lease-3512",
                ),
            )
            message = _first_message_text(
                await _build_omnigent_first_message(
                    request=bound, prompt={}, artifact_gateway=None
                )
            )
            assert inputs["instructions"] in message
            assert request.execution_profile_ref == "profile-3512"
            assert (
                bound.parameters["omnigent"]["session"]["modelOverride"] == "same-model"
            )
            assert (
                workspace / "predecessor.txt"
            ).read_bytes() == b"useful predecessor\n"
            assert (workspace / "candidate.txt").read_bytes() == (
                b"C0\n" if ordinal == 1 else b"C0\nA\n"
            )
            counts["repair"] += 1
            with (workspace / "candidate.txt").open("ab") as stream:
                stream.write(b"A\n" if ordinal == 1 else b"B\n")

            def preserve(repo):
                for name in ("predecessor.txt", "candidate.txt"):
                    (repo / name).write_bytes((workspace / name).read_bytes())

            saved, candidate_ref, candidate_checkpoint = await capture(
                ordinal, preserve, target.workflow_id, target.run_id, repair["id"]
            )
            output = RemediationAttemptOutput(
                attemptEvidenceRef=await put({"attempt": ordinal}),
                parentCheckpointRef=frozen.base_checkpoint_ref,
                parentWorkspaceDigest=frozen.expected_base_digest,
                outputCheckpointRef=candidate_ref,
                outputWorkspaceDigest=saved.workspace_evidence["workspaceDigest"],
                checkpointManifestRef="artifact://"
                + saved.workspace_evidence["manifestRef"].removeprefix("artifact://"),
                outcome="candidate_captured",
            )
            head = await branch_owner.advance_remediation_head(
                workflow_id=target.workflow_id,
                branch_id=branch.branch_id,
                attempt=frozen,
                output=output,
                step_execution_id=step_id,
                transition_id=f"attempt-{ordinal}",
            )
            workflow._advance_remediation_workspace_head(
                node=repair,
                node_inputs=inputs,
                execution_result={
                    "outputs": {
                        "remediationAttemptOutput": output.model_dump(by_alias=True)
                    }
                },
                step_execution_id=step_id,
            )
            verifier_inputs = dict(verifier["inputs"])
            workflow._inject_remediation_verification_baseline(
                node=verifier, node_inputs=verifier_inputs
            )
            assert verifier_inputs["remediationWorkspaceHeadRef"] == candidate_ref
            previous_ref, previous_checkpoint = candidate_ref, candidate_checkpoint
            gate = await put(
                {
                    "verdict": (
                        "ADDITIONAL_WORK_NEEDED"
                        if ordinal == 1
                        else "FULLY_IMPLEMENTED"
                    ),
                    "remainingWork": ["B"] if ordinal == 1 else [],
                }
            )
            remaining = await put(["B"]) if ordinal == 1 else None
            shutil.rmtree(authority)  # save precedes destructive host cleanup
            if ordinal == 1:
                # Worker replacement restores the real controller continuation,
                # including C1 and the consumed semantic attempt budget.
                workflow._original_input_payload = {"parameters": parameters}
                continuation = workflow._build_remediation_loop_continue_as_new_input(
                    ordered_nodes=nodes
                )
                replacement = MoonMindRunWorkflow()
                replacement._initialize_remediation_loop_controller(
                    ordered_nodes=[controller], require_agent_instructions=False
                )
                replacement._remediation_loop_continuation = continuation[
                    "remediation_loop_continuation"
                ]
                replacement._restore_remediation_loop_continuation(ordered_nodes=nodes)
                replacement._write_json_artifact = lambda *, name, payload: put(payload)
                workflow = replacement
                assert workflow._remediation_loop_state.consumed_budgets.attempts == 1
                saved.remove_source()
                before_retry = copy.deepcopy(counts)
                unavailable_gate = await put(
                    {
                        "verdict": "NO_DETERMINATION",
                        "reason": "verification evidence unavailable",
                    }
                )
                assert await workflow._evaluate_dynamic_remediation_verification(
                    ordered_nodes=nodes,
                    verdict="NO_DETERMINATION",
                    gate_result_ref=unavailable_gate,
                    remaining_work_ref=None,
                    logical_step_id=verifier["id"],
                    recoverable_evidence=True,
                )
                retry = nodes[-1]
                retry_inputs = dict(retry["inputs"])
                workflow._inject_remediation_verification_baseline(
                    node=retry, node_inputs=retry_inputs
                )
                assert retry_inputs["remediationWorkspaceHeadRef"] == candidate_ref
                assert retry_inputs["readOnlyWorkspaceHead"] is True
                assert retry["tool"] == verifier["tool"]
                assert workflow._remediation_loop_state.consumed_budgets.attempts == 1
                assert (
                    workflow._remediation_loop_state.consumed_budgets.evidence_retries
                    == 1
                )
                assert counts == before_retry
            await workflow._evaluate_dynamic_remediation_verification(
                ordered_nodes=nodes,
                verdict=(
                    "ADDITIONAL_WORK_NEEDED" if ordinal == 1 else "FULLY_IMPLEMENTED"
                ),
                gate_result_ref=gate,
                remaining_work_ref=remaining,
            )
        assert (
            workflow._publish_context["remediationLoop"]["consumedBudgets"]["attempts"]
            == 2
        )
        assert (
            workflow._publish_context["remediationLoop"]["consumedBudgets"][
                "evidenceRetries"
            ]
            == 1
        )
        assert (saved.source_repo / "candidate.txt").read_bytes() == b"C0\nA\nB\n"
        assert (
            target.parameters,
            target.memo,
            source._step_ledger_rows,
        ) == source_snapshot
        turn.created_step_execution_id = step_id
        await session.flush()
        handoff = build_branch_turn_verification_handoff(
            branch_id=branch.branch_id,
            branch_turn_id=turn.branch_turn_id,
            agent_result_ref=output.attempt_evidence_ref,
            diagnostics_ref=None,
            checkpoint_ref=candidate_ref,
            checkpoint_digest=head.head_workspace_digest,
            terminal_disposition="delivered_verification_pending",
            delivery_outcome="succeeded",
            source_namespace=target.namespace,
            source_workflow_id=target.workflow_id,
            source_run_id=target.run_id,
            verification_pending=True,
        )
        await branch_owner.finalize_turn_execution(
            workflow_id=target.workflow_id,
            branch_id=branch.branch_id,
            branch_turn_id=turn.branch_turn_id,
            outcome="succeeded",
            agent_result_ref=output.attempt_evidence_ref,
            diagnostics_ref=await put({"failure": diagnostic}),
            verification_handoff=handoff,
        )
        await RemediationContextBuilder(
            session=session, artifact_service=service
        ).build_context(remediation_workflow_id=remediation.workflow_id)
        action_kind = "checkpoint_branch.create_from_remediation_context"
        action_parameters = {
            "remediationWorkflowId": remediation.workflow_id,
            "remediationContextRef": objective_ref,
            "checkpointRef": root_ref,
            "instructionRef": instruction_ref,
            "instructionDigest": turn.instruction_digest,
            "repository": intent["repository"],
            "baseBranch": "main",
            "baseCommit": saved.baseline_commit,
            "logicalStepId": "implement",
            "executionOrdinal": 1,
            "expectedRunId": target.run_id,
        }
        authority_result = await RemediationActionAuthorityService(
            session=session
        ).evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind=action_kind,
            parameters=action_parameters,
            dry_run=False,
            idempotency_key="3512-action",
            requesting_principal="workflow:remediator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(allowed_action_kinds=(action_kind,)),
        )
        guard = await RemediationMutationGuardService(session=session).evaluate(
            remediation_workflow_id=remediation.workflow_id,
            remediation_run_id=remediation.run_id,
            target_workflow_id=target.workflow_id,
            target_run_id=target.run_id,
            action_kind=action_kind,
            idempotency_key="3512-action",
            parameters=action_parameters,
            policy=RemediationMutationGuardPolicy(cooldown_seconds=0),
            now=now,
        )
        # Controlled external action acceptance names the actual persisted
        # candidate produced above. The action/result, observer and continuation
        # owners are production code, not an alternate verification model.
        executor = AcceptedEffect(
            {
                "workflowId": target.workflow_id,
                "runId": target.run_id,
                "branchId": branch.branch_id,
                "branchTurnId": turn.branch_turn_id,
                "stepExecutionId": step_id,
            }
        )
        action_args = dict(
            remediation_workflow_id=remediation.workflow_id,
            authority_result=authority_result.to_dict(),
            guard_result=guard.to_dict(),
            principal="service:test",
            admitted_principal="service:remediation-context",
        )
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=service,
            action_executor=executor,
            verification_phase=_fast_verification_phase(session),
        )
        pending = await tools.execute_action(**action_args)
        assert pending["verification"]["pending"] is True
        assert pending["verification"]["outcome"] is None
        assert executor.effects == 1
        await session.commit()
        # Replace the SQL/service owner as well as the workspace host. The
        # verifier notification resumes the saved action without an open page.
        async with AsyncSession(
            bind=session.bind, expire_on_commit=False
        ) as replacement_session:
            replacement_artifacts = TemporalArtifactService(
                TemporalArtifactRepository(replacement_session), store=service._store
            )
            replacement_owner = CheckpointBranchService(replacement_session)
            verification = VerificationEvidence(
                inputHeadRef=candidate_ref,
                inputHeadDigest=head.head_workspace_digest,
                inputHeadVersion=head.head_version,
                preVerificationWorkspaceDigest=head.head_workspace_digest,
                postVerificationWorkspaceDigest=head.head_workspace_digest,
                verifierArtifactRef=gate,
                verdict="FULLY_IMPLEMENTED",
            )
            with pytest.raises(RemediationHeadError):
                await replacement_owner.record_remediation_verification(
                    workflow_id=target.workflow_id,
                    branch_id=branch.branch_id,
                    evidence=verification.model_copy(
                        update={"input_head_ref": root_ref}
                    ),
                )
            await replacement_owner.record_remediation_verification(
                workflow_id=target.workflow_id,
                branch_id=branch.branch_id,
                evidence=verification,
            )
            link = await replacement_session.get(
                models.TemporalExecutionRemediationLink, remediation.workflow_id
            )
            assert link.verification_outcome == "verified_resolved"
            result = await RemediationEvidenceToolService(
                session=replacement_session,
                artifact_service=replacement_artifacts,
                action_executor=executor,
                verification_phase=_fast_verification_phase(replacement_session),
            ).execute_action(**action_args)
            assert executor.effects == 1
            assert (
                result["verification"]["resultingIdentity"]["checkpointRef"]
                == candidate_ref
            )
            receipt = await _read_artifact_json(
                replacement_artifacts, result["artifactRefs"]["verification"]
            )
            assert receipt["outcome"] == "verified_resolved"
            failed = await replacement_session.get(
                models.TemporalExecutionCanonicalRecord, target.workflow_id
            )
            assert failed.state == models.MoonMindWorkflowState.FAILED
            assert failed.parameters == source_snapshot[0]
            assert failed.memo["failureDiagnostic"] == diagnostic
            await replacement_session.commit()
        evidence.update(
            target=target.workflow_id,
            candidate=candidate_ref,
            head=head,
            counts=copy.deepcopy(counts),
        )
        return saved

    async with journey(
        tmp_path,
        monkeypatch,
        destination_files={"README.md": "destination\n"},
        saved_work_factory=compute_and_save,
    ) as state:
        monkeypatch.setattr(settings.oidc, "AUTH_PROVIDER", "oidc")
        before = state.saved_bytes()
        retained_claims = {
            (claim.artifact_id, claim.operation_kind, claim.owner_principal)
            for claim in await state.use_claims()
        }
        contract = state.contract(
            objective="pr", baseBranch="main", strategy="additive_import"
        )
        state.provider.unavailable_reads = 5
        with pytest.raises(ApplicationError):
            await state.run(contract)
        first = await _persisted_result(state)
        assert first["push"]["status"] == "pushed"
        published_head = git(state.remote, "rev-parse", "refs/heads/saved/work")
        result = await state.run(contract)
        assert result["outcome"] == "published"
        assert result["candidate"] == first["candidate"]
        assert result["implementationRerun"] is False
        assert _tree(state.remote, published_head)["candidate.txt"] == "C0\nA\nB"
        assert (
            _tree(state.remote, published_head)["predecessor.txt"]
            == "useful predecessor"
        )
        assert len(state.pushes()) == len(state.provider.creates) == 1
        assert (
            counts
            == evidence["counts"]
            == {"prepare": 1, "failed_compute": 1, "repair": 2, "save": 3, "restore": 2}
        )
        assert state.saved_objects_unchanged(before)
        assert {
            (claim.artifact_id, claim.operation_kind, claim.owner_principal)
            for claim in await state.use_claims()
        } == retained_claims
        assert all(kind != "publication" for _, kind, _ in retained_claims)
