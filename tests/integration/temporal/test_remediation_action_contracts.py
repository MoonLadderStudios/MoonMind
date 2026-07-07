"""Hermetic integration coverage for remediation action evidence contracts."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from api_service.db.models import (
    TemporalArtifactLink,
    TemporalExecutionCanonicalRecord,
    WorkflowCheckpointBranchTurn,
)
from api_service.services.checkpoint_branch_service import CheckpointBranchService
from moonmind.workflows.temporal import (
    LocalTemporalArtifactStore,
    TemporalArtifactRepository,
    TemporalArtifactService,
)
from moonmind.workflows.temporal.remediation_actions import (
    RemediationActionAuthorityService,
    RemediationMutationGuardPolicy,
    RemediationMutationGuardService,
)
from moonmind.workflows.temporal.remediation_context import (
    RemediationContextBuilder,
    build_remediation_prevention_outcome,
    build_remediation_repair_decision,
    build_remediation_summary_block,
)
from moonmind.workflows.temporal.remediation_tools import (
    RemediationEvidenceToolError,
    RemediationEvidenceToolService,
)
from tests.unit.workflows.temporal.test_remediation_context import (
    RecordingActionExecutor,
    _admin_permissions,
    _admin_profile,
    _create_target_and_remediation,
    _read_artifact_json,
    mock_client_adapter,
    temporal_db,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]


async def test_remediation_action_contract_publishes_request_result_and_verification(
    tmp_path, mock_client_adapter
) -> None:
    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        artifact_service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        await RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        ).build_context(remediation_workflow_id=remediation.workflow_id)

        action_kind = "workload.restart_helper_container"
        action_id = "integration-action-contract"
        authority = await RemediationActionAuthorityService(
            session=session
        ).evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind=action_kind,
            parameters={"reason": "restart helper"},
            dry_run=False,
            idempotency_key=action_id,
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
            idempotency_key=action_id,
            parameters={"reason": "restart helper"},
            policy=RemediationMutationGuardPolicy(cooldown_seconds=0),
            now=datetime(2026, 4, 23, tzinfo=timezone.utc),
        )
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifact_service,
            action_executor=RecordingActionExecutor(),
        )

        result = await tools.execute_action(
            remediation_workflow_id=remediation.workflow_id,
            authority_result=authority.to_dict(),
            guard_result=guard.to_dict(),
            principal="service:test",
        )

        request_payload = await _read_artifact_json(
            artifact_service, result["artifactRefs"]["actionRequest"]
        )
        result_payload = await _read_artifact_json(
            artifact_service, result["artifactRefs"]["actionResult"]
        )
        verification_payload = await _read_artifact_json(
            artifact_service, result["artifactRefs"]["verification"]
        )
        audit_payload = await _read_artifact_json(
            artifact_service, result["artifactRefs"]["auditEvent"]
        )
        annotation_payload = await _read_artifact_json(
            artifact_service, result["artifactRefs"]["targetAnnotation"]
        )

        assert request_payload["schemaVersion"] == "v1"
        assert request_payload["actionKind"] == action_kind
        assert request_payload["target"]["workflowId"] == target.workflow_id
        assert request_payload["idempotencyKey"] == action_id
        assert result_payload["schemaVersion"] == "v1"
        assert result_payload["status"] == "applied"
        assert result_payload["message"]
        assert result_payload["appliedAt"]
        assert result_payload["verificationRequired"] is True
        assert result_payload["verificationHint"]
        assert verification_payload["actionKind"] == action_kind
        assert verification_payload["actionId"] == action_id
        assert audit_payload["eventType"] == "remediation.action"
        assert audit_payload["remediationWorkflowId"] == remediation.workflow_id
        assert audit_payload["targetWorkflowId"] == target.workflow_id
        assert audit_payload["actionKind"] == action_kind
        assert annotation_payload["kind"] == "remediation.target_annotation"
        assert annotation_payload["targetWorkflowId"] == target.workflow_id
        assert annotation_payload["remediationWorkflowId"] == remediation.workflow_id
        assert (
            annotation_payload["artifactRefs"]["actionRequest"]
            == result["artifactRefs"]["actionRequest"]
        )

        audit_links = (
            await session.execute(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.workflow_id == remediation.workflow_id,
                    TemporalArtifactLink.link_type == "remediation.audit_event",
                )
            )
        ).scalars().all()
        target_annotation_links = (
            await session.execute(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.workflow_id == target.workflow_id,
                    TemporalArtifactLink.link_type == "remediation.target_annotation",
                )
            )
        ).scalars().all()
        assert len(audit_links) == 1
        assert len(target_annotation_links) == 1


async def test_remediation_checkpoint_branch_action_creates_fresh_session_turn(
    tmp_path, mock_client_adapter
) -> None:
    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
            action_policy_ref="checkpoint_branch_repair",
        )
        target_source = await session.get(
            TemporalExecutionCanonicalRecord, target.workflow_id
        )
        assert target_source is not None
        target_source.artifact_refs = [
            "art_checkpoint_after_implement",
            "art_step_execution_manifest",
            "art_diagnostics_branch_launch",
        ]
        await session.commit()
        artifact_service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        await RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        ).build_context(remediation_workflow_id=remediation.workflow_id)
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifact_service,
        )

        result = await tools.create_checkpoint_branch_from_remediation_context(
            remediation_workflow_id=remediation.workflow_id,
            checkpoint_branch_service=CheckpointBranchService(session),
            principal="service:test",
            request={
                "reason": "Try corrected test command",
                "source": {
                    "runId": target.run_id,
                    "logicalStepId": "implement",
                    "executionOrdinal": 2,
                    "checkpointBoundary": "after_execution",
                    "checkpointRef": "art_checkpoint_after_implement",
                    "checkpointDigest": "sha256:checkpoint",
                },
                "instructions": {"text": "Run the corrected focused backend tests."},
                "branch": {
                    "branchId": "cbr-remediation-focused",
                    "branchTurnId": "cbr-remediation-focused:turn:1",
                    "label": "Focused remediation repair",
                    "workspacePolicy": (
                        "apply_previous_execution_diff_to_clean_baseline"
                    ),
                    "runtimeContextPolicy": "fresh_agent_run",
                },
                "launch": {
                    "createdStepExecutionId": "step-exec-remediation-1",
                    "stepExecutionManifestRef": "art_step_execution_manifest",
                    "diagnosticsRef": "art_diagnostics_branch_launch",
                },
            },
        )

        assert result["status"] == "applied"
        assert result["actionKind"] == "checkpoint_branch.create_from_remediation_context"
        assert result["branchId"] == "cbr-remediation-focused"
        turn = (
            await session.execute(
                select(WorkflowCheckpointBranchTurn).where(
                    WorkflowCheckpointBranchTurn.branch_turn_id
                    == "cbr-remediation-focused:turn:1"
                )
            )
        ).scalar_one()
        assert turn.status == "running"
        assert turn.created_step_execution_id == "step-exec-remediation-1"
        assert turn.runtime_context_policy == "fresh_agent_run"
        instruction_payload = await _read_artifact_json(
            artifact_service, result["artifactRefs"]["instructionArtifact"]
        )
        provenance_payload = await _read_artifact_json(
            artifact_service, result["artifactRefs"]["provenance"]
        )
        assert instruction_payload["immutable"] is True
        assert provenance_payload["retryActionKind"] == (
            "checkpoint_branch.create_from_remediation_context"
        )
        assert provenance_payload["metadata"]["branchId"] == "cbr-remediation-focused"

        with pytest.raises(RemediationEvidenceToolError, match="raw provider"):
            await tools.create_checkpoint_branch_from_remediation_context(
                remediation_workflow_id=remediation.workflow_id,
                checkpoint_branch_service=CheckpointBranchService(session),
                request={
                    "source": {
                        "runId": target.run_id,
                        "checkpointBoundary": "after_execution",
                        "checkpointRef": "omnigent://native/checkpoint",
                    },
                    "instructions": {"text": "Unsafe"},
                    "launch": {
                        "createdStepExecutionId": "step-exec-remediation-unsafe",
                        "stepExecutionManifestRef": "art_step_execution_manifest",
                        "diagnosticsRef": "art_diagnostics_branch_launch",
                    },
                },
            )


async def test_remediation_raw_action_rejection_does_not_publish_side_effect_artifacts(
    tmp_path, mock_client_adapter
) -> None:
    async with temporal_db(tmp_path) as session:
        _target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        service = RemediationActionAuthorityService(session=session)

        decision = await service.evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind="redaction_bypass",
            parameters={"token": "raw-secret-token"},
            dry_run=False,
            idempotency_key="raw-integration",
            requesting_principal="user:operator",
            permissions=_admin_permissions(),
            security_profile=_admin_profile(),
        )

        links = (
            await session.execute(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.workflow_id == remediation.workflow_id,
                    TemporalArtifactLink.link_type.in_(
                        [
                            "remediation.action_request",
                            "remediation.action_result",
                        ]
                    ),
                )
            )
        ).scalars().all()

        assert decision.decision == "denied"
        assert decision.reason == "raw_access_action_denied"
        assert decision.executable is False
        assert "raw-secret-token" not in json.dumps(decision.to_dict(), sort_keys=True)
        assert links == []


async def test_remediation_lifecycle_repair_prevention_summary_artifacts(
    tmp_path, mock_client_adapter
) -> None:
    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="admin_auto",
        )
        artifact_service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        await RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        ).build_context(remediation_workflow_id=remediation.workflow_id)

        action_kind = "workload.restart_helper_container"
        action_id = "integration-lifecycle-repair"
        authority = await RemediationActionAuthorityService(
            session=session
        ).evaluate_action_request(
            remediation_workflow_id=remediation.workflow_id,
            action_kind=action_kind,
            parameters={"reason": "restart helper"},
            dry_run=False,
            idempotency_key=action_id,
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
            idempotency_key=action_id,
            parameters={"reason": "restart helper"},
            policy=RemediationMutationGuardPolicy(cooldown_seconds=0),
            now=datetime(2026, 4, 23, tzinfo=timezone.utc),
        )
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifact_service,
            action_executor=RecordingActionExecutor(),
        )

        result = await tools.execute_action(
            remediation_workflow_id=remediation.workflow_id,
            authority_result=authority.to_dict(),
            guard_result=guard.to_dict(),
            principal="service:test",
        )

        repair = build_remediation_repair_decision(
            target_workflow_id=target.workflow_id,
            pinned_run_id=target.run_id,
            current_run_id=target.run_id,
            candidate_action_kind=action_kind,
            candidate_reason="helper_container_unhealthy",
            decision="attempted",
            decision_reason="fresh_target_health_and_policy_allowed",
            repair_outcome="repaired",
            action_request_ref=result["artifactRefs"]["actionRequest"],
            action_result_ref=result["artifactRefs"]["actionResult"],
            verification_ref=result["artifactRefs"]["verification"],
        )
        prevention = build_remediation_prevention_outcome(
            status="findings_reported",
            root_cause_category="helper_container_health_gap",
            summary="Findings were recorded for follow-up prevention.",
            findings_ref=result["artifactRefs"]["verification"],
        )
        summary_result = await tools.publish_lifecycle_summary(
            remediation_workflow_id=remediation.workflow_id,
            repair=repair,
            prevention=prevention,
            summary=build_remediation_summary_block(
                target_workflow_id=target.workflow_id,
                target_run_id=target.run_id,
                phase="resolved",
                mode="snapshot_then_follow",
                authority_mode="admin_auto",
                resolution="resolved_after_action",
            ),
            decision_log_entries=(
                {
                    "timestamp": "2026-05-08T00:00:00Z",
                    "phase": "verifying",
                    "decisionType": "repair_candidate",
                    "decision": "attempted",
                    "reason": "fresh_target_health_and_policy_allowed",
                    "actor": "service:remediation",
                    "actionKind": action_kind,
                    "targetWorkflowId": target.workflow_id,
                    "targetRunId": target.run_id,
                    "artifactRefs": result["artifactRefs"],
                },
                {
                    "timestamp": "2026-05-08T00:00:01Z",
                    "phase": "resolved",
                    "decisionType": "prevention",
                    "decision": "findings_reported",
                    "reason": "findings_recorded",
                    "actor": "service:remediation",
                    "targetWorkflowId": target.workflow_id,
                    "targetRunId": target.run_id,
                    "artifactRefs": {"findings": result["artifactRefs"]["verification"]},
                },
            ),
            lock_release="released",
            principal="service:test",
        )

        summary_payload = await _read_artifact_json(
            artifact_service, summary_result["artifactRefs"]["summary"]
        )
        assert summary_payload["repair"]["repairOutcome"] == "repaired"
        assert summary_payload["prevention"]["status"] == "findings_reported"
        assert (
            summary_payload["decisionLogRef"]
            == summary_result["artifactRefs"]["decisionLog"]
        )


async def test_remediation_lifecycle_cancellation_and_continuity_summary_artifacts(
    tmp_path, mock_client_adapter
) -> None:
    async with temporal_db(tmp_path) as session:
        target, remediation = await _create_target_and_remediation(
            session,
            mock_client_adapter,
            authority_mode="approval_gated",
        )
        artifact_service = TemporalArtifactService(
            TemporalArtifactRepository(session),
            store=LocalTemporalArtifactStore(tmp_path / "artifacts"),
        )
        await RemediationContextBuilder(
            session=session,
            artifact_service=artifact_service,
        ).build_context(remediation_workflow_id=remediation.workflow_id)
        tools = RemediationEvidenceToolService(
            session=session,
            artifact_service=artifact_service,
        )
        repair = build_remediation_repair_decision(
            target_workflow_id=target.workflow_id,
            pinned_run_id=target.run_id,
            decision="escalated",
            decision_reason="canceled_before_action",
            repair_outcome="escalated",
        )
        prevention = build_remediation_prevention_outcome(
            status="policy_blocked",
            root_cause_category="not_evaluated",
            summary="Cancellation blocked recurrence-prevention changes.",
            blocked_reason="remediation_canceled",
        )
        summary_result = await tools.publish_lifecycle_summary(
            remediation_workflow_id=remediation.workflow_id,
            repair=repair,
            prevention=prevention,
            summary=build_remediation_summary_block(
                target_workflow_id=target.workflow_id,
                target_run_id=target.run_id,
                phase="escalated",
                mode="snapshot_then_follow",
                authority_mode="approval_gated",
                resolution="escalated",
                escalated=True,
                resulting_target_run_id="target-run-after-cancel",
            ),
            decision_log_entries=(
                {
                    "timestamp": "2026-05-08T00:00:00Z",
                    "phase": "escalated",
                    "decisionType": "cancellation",
                    "decision": "escalated",
                    "reason": "canceled_before_action",
                    "actor": "service:remediation",
                    "targetWorkflowId": target.workflow_id,
                    "targetRunId": target.run_id,
                    "artifactRefs": {},
                },
            ),
            lock_release="attempted",
            principal="service:test",
        )

        summary_payload = await _read_artifact_json(
            artifact_service, summary_result["artifactRefs"]["summary"]
        )
        assert summary_payload["repair"]["repairOutcome"] == "escalated"
        assert summary_payload["prevention"]["status"] == "policy_blocked"
        assert summary_payload["lockRelease"] == "attempted"
        assert summary_payload["resultingTargetRunId"] == "target-run-after-cancel"
        links = (
            await session.execute(
                select(TemporalArtifactLink).where(
                    TemporalArtifactLink.workflow_id == remediation.workflow_id,
                    TemporalArtifactLink.link_type == "remediation.action_request",
                )
            )
        ).scalars().all()
        assert links == []
