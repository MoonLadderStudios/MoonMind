"""Durable reviewer approval authority for remediation actions.

The database row, not an opaque caller string, is the authority boundary.
Issue MoonLadderStudios/MoonMind#3620.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db.models import (
    RemediationApproval,
    TemporalExecutionCanonicalRecord,
    TemporalExecutionRemediationLink,
)
from moonmind.utils.logging import redact_sensitive_payload
from moonmind.workflows.temporal.remediation_actions import remediation_action_risk


class RemediationApprovalError(ValueError):
    """A bounded fail-closed approval error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_expired(expires_at: datetime, now: datetime) -> bool:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= now


def approval_to_dict(row: RemediationApproval) -> dict[str, Any]:
    return {
        "approvalId": row.approval_id,
        "requestDigest": row.request_digest,
        "remediationWorkflowId": row.remediation_workflow_id,
        "remediationRunId": row.remediation_run_id,
        "targetWorkflowId": row.target_workflow_id,
        "targetRunId": row.target_run_id,
        "actionKind": row.action_kind,
        "riskTier": row.risk_tier,
        "redactedParameters": dict(row.redacted_parameters),
        "parameterDigest": row.parameter_digest,
        "authorityBinding": dict(row.authority_binding),
        "approvalClass": row.approval_class,
        "reviewerRule": row.reviewer_rule,
        "requestingActor": row.requesting_actor,
        "decisionActor": row.decision_actor,
        "rationale": row.rationale,
        "status": row.status,
        "requestedAt": row.requested_at,
        "decidedAt": row.decided_at,
        "expiresAt": row.expires_at,
        "consumedAt": row.consumed_at,
        "consumedByActionId": row.consumed_by_action_id,
        "actionArtifactRef": row.action_artifact_ref,
        "auditArtifactRef": row.audit_artifact_ref,
        "verificationArtifactRef": row.verification_artifact_ref,
    }


class RemediationApprovalService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def request(
        self,
        *,
        remediation_workflow_id: str,
        idempotency_key: str,
        action_kind: str,
        risk_tier: str,
        redacted_parameters: Mapping[str, Any],
        authority_binding: Mapping[str, Any],
        approval_class: str,
        reviewer_rule: str,
        requesting_actor: str,
        ttl_seconds: int = 3600,
    ) -> RemediationApproval:
        link = await self._session.get(
            TemporalExecutionRemediationLink, remediation_workflow_id
        )
        if link is None:
            raise RemediationApprovalError("approval_target_not_found")
        target = await self._session.get(
            TemporalExecutionCanonicalRecord, link.target_workflow_id
        )
        if target is None:
            raise RemediationApprovalError("approval_target_not_found")
        if target.run_id != link.target_run_id:
            raise RemediationApprovalError("approval_target_run_mismatch")
        if ttl_seconds < 1 or ttl_seconds > 86400:
            raise RemediationApprovalError("approval_expiration_invalid")
        canonical_risk = remediation_action_risk(action_kind)
        if canonical_risk is None or risk_tier != canonical_risk:
            raise RemediationApprovalError("approval_action_invalid")
        if not approval_class.strip() or not reviewer_rule.strip():
            raise RemediationApprovalError("approval_policy_invalid")
        if not requesting_actor.strip() or not idempotency_key.strip():
            raise RemediationApprovalError("approval_request_identity_invalid")
        redacted = redact_sensitive_payload(dict(redacted_parameters))
        params = redacted if isinstance(redacted, dict) else {}
        binding = dict(authority_binding)
        supplied_target_run = binding.get("targetRunId")
        if supplied_target_run is not None and supplied_target_run != link.target_run_id:
            raise RemediationApprovalError("approval_target_run_mismatch")
        binding.setdefault("targetRunId", link.target_run_id)
        target_state = getattr(target.state, "value", target.state)
        supplied_target_state = binding.get("targetExpectedState")
        if supplied_target_state is not None and supplied_target_state != target_state:
            raise RemediationApprovalError("approval_target_state_mismatch")
        binding.setdefault("targetExpectedState", target_state)
        request = {
            "remediationWorkflowId": remediation_workflow_id,
            "remediationRunId": link.remediation_run_id,
            "targetWorkflowId": link.target_workflow_id,
            "targetRunId": link.target_run_id,
            "actionKind": action_kind,
            "riskTier": risk_tier,
            "parameterDigest": _digest(params),
            "authorityBinding": binding,
            "approvalClass": approval_class,
            "reviewerRule": reviewer_rule,
            "requestingActor": requesting_actor,
        }
        request_digest = _digest(request)
        existing = (
            await self._session.execute(
                select(RemediationApproval).where(
                    RemediationApproval.remediation_workflow_id
                    == remediation_workflow_id,
                    RemediationApproval.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.request_digest != request_digest:
                raise RemediationApprovalError("approval_idempotency_conflict")
            return existing
        now = datetime.now(timezone.utc)
        row = RemediationApproval(
            approval_id=f"approval:{uuid4()}",
            idempotency_key=idempotency_key,
            request_digest=request_digest,
            remediation_workflow_id=remediation_workflow_id,
            remediation_run_id=link.remediation_run_id,
            target_workflow_id=link.target_workflow_id,
            target_run_id=link.target_run_id,
            action_kind=action_kind,
            risk_tier=risk_tier,
            redacted_parameters=params,
            parameter_digest=request["parameterDigest"],
            authority_binding=binding,
            approval_class=approval_class,
            reviewer_rule=reviewer_rule,
            requesting_actor=requesting_actor,
            status="pending",
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            concurrent = (
                await self._session.execute(
                    select(RemediationApproval).where(
                        RemediationApproval.remediation_workflow_id
                        == remediation_workflow_id,
                        RemediationApproval.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if concurrent is None or concurrent.request_digest != request_digest:
                raise RemediationApprovalError("approval_idempotency_conflict")
            return concurrent
        await self._session.refresh(row)
        return row

    async def decide(
        self,
        *,
        approval_id: str,
        decision: str,
        actor: str,
        rationale: str | None,
        reviewer_is_workflow_owner: bool,
        can_approve_high_risk: bool,
    ) -> RemediationApproval:
        row = await self._locked(approval_id)
        now = datetime.now(timezone.utc)
        if row.status in {"approved", "denied"}:
            expected = "approved" if decision == "approved" else "denied"
            if row.status == expected and row.decision_actor == actor:
                return row
            raise RemediationApprovalError("approval_already_decided")
        if row.status != "pending":
            raise RemediationApprovalError(f"approval_{row.status}")
        if _is_expired(row.expires_at, now):
            row.status = "expired"
            await self._session.commit()
            raise RemediationApprovalError("approval_expired")
        if row.reviewer_rule in {"owner", "workflow-owner"}:
            if not reviewer_is_workflow_owner:
                raise RemediationApprovalError("approval_reviewer_rule_not_satisfied")
        elif row.reviewer_rule in {"different_actor", "separation-of-duty"}:
            if actor == row.requesting_actor:
                raise RemediationApprovalError("approval_self_review_forbidden")
        else:
            raise RemediationApprovalError("approval_reviewer_rule_unsupported")
        if row.risk_tier == "high" and not can_approve_high_risk:
            raise RemediationApprovalError("approval_high_risk_permission_required")
        if decision not in {"approved", "denied"}:
            raise RemediationApprovalError("approval_decision_invalid")
        row.status = decision
        row.decision_actor = actor
        row.rationale = rationale[:500] if rationale else None
        row.decided_at = now
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def resolve_and_consume(
        self, *, approval_id: str, action_id: str, action_kind: str,
        risk_tier: str, redacted_parameters: Mapping[str, Any],
        current_binding: Mapping[str, Any],
    ) -> RemediationApproval:
        row = await self._locked(approval_id)
        now = datetime.now(timezone.utc)
        if row.status == "consumed" and row.consumed_by_action_id == action_id:
            return row
        if row.status != "approved":
            raise RemediationApprovalError(f"approval_{row.status}")
        if _is_expired(row.expires_at, now):
            row.status = "expired"
            await self._session.commit()
            raise RemediationApprovalError("approval_expired")
        if (
            row.action_kind != action_kind
            or row.risk_tier != risk_tier
            or row.parameter_digest != _digest(dict(redacted_parameters))
        ):
            row.status = "stale"
            await self._session.commit()
            raise RemediationApprovalError("approval_action_mismatch")
        for key, expected in row.authority_binding.items():
            if current_binding.get(key) != expected:
                row.status = "stale"
                await self._session.commit()
                raise RemediationApprovalError(f"approval_stale_{key}")
        row.status = "consumed"
        row.consumed_at = now
        row.consumed_by_action_id = action_id
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def _locked(self, approval_id: str) -> RemediationApproval:
        row = (
            await self._session.execute(
                select(RemediationApproval)
                .where(RemediationApproval.approval_id == approval_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise RemediationApprovalError("approval_not_found")
        return row
