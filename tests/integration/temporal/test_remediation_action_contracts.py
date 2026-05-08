"""Hermetic integration coverage for remediation action evidence contracts."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from api_service.db.models import TemporalArtifactLink
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
from moonmind.workflows.temporal.remediation_context import RemediationContextBuilder
from moonmind.workflows.temporal.remediation_tools import RemediationEvidenceToolService
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
