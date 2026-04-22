"""Remediation action authority decisions.

This module intentionally evaluates whether a remediation action may proceed; it
does not execute host, container, SQL, provider, or storage operations.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from moonmind.utils.logging import redact_sensitive_payload, redact_sensitive_text

RemediationActionRisk = Literal["low", "medium", "high"]
RemediationActionDecision = Literal[
    "allowed",
    "approval_required",
    "dry_run_only",
    "denied",
]

_RISK_ORDER: dict[str, int] = {"low": 1, "medium": 2, "high": 3}
_RAW_ACCESS_ACTION_KINDS = frozenset(
    {
        "raw_host_shell",
        "host_shell",
        "docker_daemon",
        "raw_docker",
        "sql_database",
        "raw_sql",
        "storage_key_read",
        "raw_storage",
    }
)
_ACTION_CATALOG: dict[str, dict[str, Any]] = {
    "restart_worker": {
        "risk": "medium",
        "enabled": True,
        "target_type": "workload_container",
        "input_metadata": {
            "reason": {"type": "string", "required": False},
        },
        "verification_hint": "verify helper container health and target state",
    },
    "terminate_session": {
        "risk": "high",
        "enabled": True,
        "target_type": "managed_session",
        "input_metadata": {
            "reason": {"type": "string", "required": False},
        },
        "verification_hint": "verify session termination state and target run status",
    },
}
_DEFAULT_AUTO_ALLOWED_RISK = "medium"
_SUPPORTED_AUTHORITY_MODES = frozenset(
    {"observe_only", "approval_gated", "admin_auto"}
)
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"/(?:[A-Za-z0-9._:@+-]+/)*[A-Za-z0-9._:@+-]+"
)
_PRESIGNED_URL_PATTERN = re.compile(
    r"https?://[^\s\"']*(?:token|signature|x-amz-signature|credential)[^\s\"']*",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RemediationPermissionSet:
    """Compact caller permissions for remediation action decisions."""

    can_view_target: bool = False
    can_create_remediation: bool = False
    can_request_admin_profile: bool = False
    can_approve_high_risk: bool = False
    can_inspect_audit: bool = False


@dataclass(frozen=True, slots=True)
class RemediationSecurityProfile:
    """Named elevated execution identity used for privileged remediation."""

    profile_ref: str
    execution_principal: str
    allowed_action_kinds: Sequence[str] = field(default_factory=tuple)
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class RemediationActionAuthorityResult:
    """Result of evaluating one remediation action request."""

    remediation_workflow_id: str
    target_workflow_id: str | None
    authority_mode: str | None
    action_kind: str
    risk: RemediationActionRisk | None
    decision: RemediationActionDecision
    reason: str
    idempotency_key: str
    security_profile_ref: str | None
    approval_ref: str | None
    executable: bool
    redacted_parameters: Mapping[str, Any]
    audit: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        result_status = _result_status(self.decision, self.reason)
        verification_hint = _verification_hint(self.action_kind)
        verification_required = self.executable and self.reason != "dry_run"
        return {
            "schemaVersion": "v1",
            "remediationWorkflowId": self.remediation_workflow_id,
            "targetWorkflowId": self.target_workflow_id,
            "authorityMode": self.authority_mode,
            "actionKind": self.action_kind,
            "risk": self.risk,
            "decision": self.decision,
            "reason": self.reason,
            "idempotencyKey": self.idempotency_key,
            "securityProfileRef": self.security_profile_ref,
            "approvalRef": self.approval_ref,
            "executable": self.executable,
            "redactedParameters": dict(self.redacted_parameters),
            "request": {
                "schemaVersion": "v1",
                "actionId": self.idempotency_key,
                "actionKind": self.action_kind,
                "requester": self.audit.get("requestingPrincipal"),
                "target": {
                    "workflowId": self.target_workflow_id,
                    "resourceKind": _target_type(self.action_kind),
                },
                "riskTier": self.risk,
                "dryRun": self.reason == "dry_run",
                "idempotencyKey": self.idempotency_key,
                "params": dict(self.redacted_parameters),
            },
            "result": {
                "schemaVersion": "v1",
                "actionId": self.idempotency_key,
                "status": result_status,
                "appliedAt": None,
                "beforeStateRef": None,
                "afterStateRef": None,
                "verificationRequired": verification_required,
                "verificationHint": verification_hint if verification_required else None,
                "sideEffects": [],
            },
            "audit": dict(self.audit),
        }


class RemediationActionAuthorityService:
    """Evaluate remediation action requests against authority boundaries."""

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session
        self._decisions: dict[tuple[str, str], RemediationActionAuthorityResult] = {}

    def list_allowed_actions(
        self,
        *,
        permissions: RemediationPermissionSet,
        security_profile: RemediationSecurityProfile | None,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return enabled action metadata allowed by caller permissions and profile."""

        if (
            not permissions.can_view_target
            or not permissions.can_request_admin_profile
            or security_profile is None
            or not security_profile.enabled
        ):
            return ()

        allowed_by_profile = set(security_profile.allowed_action_kinds)
        actions: list[Mapping[str, Any]] = []
        for action_kind, action_info in _ACTION_CATALOG.items():
            if not action_info.get("enabled", False):
                continue
            if action_kind not in allowed_by_profile:
                continue
            actions.append(
                {
                    "actionKind": action_kind,
                    "riskTier": action_info["risk"],
                    "targetType": action_info["target_type"],
                    "inputMetadata": deepcopy(action_info.get("input_metadata") or {}),
                    "verificationRequired": True,
                    "verificationHint": action_info["verification_hint"],
                }
            )
        return tuple(actions)

    async def evaluate_action_request(
        self,
        *,
        remediation_workflow_id: str,
        action_kind: str,
        parameters: Mapping[str, Any] | None,
        dry_run: bool,
        idempotency_key: str,
        requesting_principal: str,
        permissions: RemediationPermissionSet,
        security_profile: RemediationSecurityProfile | None = None,
        approval_ref: str | None = None,
    ) -> RemediationActionAuthorityResult:
        workflow_id = str(remediation_workflow_id or "").strip()
        idem = str(idempotency_key or "").strip()
        normalized_action = str(action_kind or "").strip()
        cache_key = (workflow_id, idem, normalized_action, dry_run)
        if workflow_id and idem and cache_key in self._decisions:
            return self._decisions[cache_key]

        if not workflow_id:
            return self._result(
                remediation_workflow_id="",
                target_workflow_id=None,
                authority_mode=None,
                action_kind=normalized_action,
                risk=None,
                decision="denied",
                reason="remediation_workflow_id_required",
                idempotency_key=idem,
                requesting_principal=requesting_principal,
                security_profile=security_profile,
                approval_ref=approval_ref,
                parameters=parameters,
            )
        if not idem:
            return self._result(
                remediation_workflow_id=workflow_id,
                target_workflow_id=None,
                authority_mode=None,
                action_kind=normalized_action,
                risk=None,
                decision="denied",
                reason="idempotency_key_required",
                idempotency_key=idem,
                requesting_principal=requesting_principal,
                security_profile=security_profile,
                approval_ref=approval_ref,
                parameters=parameters,
            )

        link = await self._session.get(
            db_models.TemporalExecutionRemediationLink,
            workflow_id,
        )
        if link is None:
            result = self._result(
                remediation_workflow_id=workflow_id,
                target_workflow_id=None,
                authority_mode=None,
                action_kind=normalized_action,
                risk=None,
                decision="denied",
                reason="remediation_link_not_found",
                idempotency_key=idem,
                requesting_principal=requesting_principal,
                security_profile=security_profile,
                approval_ref=approval_ref,
                parameters=parameters,
                summary="Remediation link was not found.",
            )
            self._decisions[cache_key] = result
            return result

        result = self._evaluate_with_link(
            link=link,
            action_kind=normalized_action,
            parameters=parameters,
            dry_run=dry_run,
            idempotency_key=idem,
            requesting_principal=requesting_principal,
            permissions=permissions,
            security_profile=security_profile,
            approval_ref=approval_ref,
        )
        self._decisions[cache_key] = result
        return result

    def _evaluate_with_link(
        self,
        *,
        link: db_models.TemporalExecutionRemediationLink,
        action_kind: str,
        parameters: Mapping[str, Any] | None,
        dry_run: bool,
        idempotency_key: str,
        requesting_principal: str,
        permissions: RemediationPermissionSet,
        security_profile: RemediationSecurityProfile | None,
        approval_ref: str | None,
    ) -> RemediationActionAuthorityResult:
        authority_mode = str(link.authority_mode or "observe_only").strip()
        action_info = _ACTION_CATALOG.get(action_kind)
        raw_access = action_kind in _RAW_ACCESS_ACTION_KINDS or action_kind.startswith(
            "raw_"
        )
        risk = (
            action_info.get("risk")
            if action_info is not None and action_info.get("risk") in _RISK_ORDER
            else None
        )

        if raw_access:
            return self._linked_result(
                link=link,
                action_kind=action_kind,
                risk=risk,
                decision="denied",
                reason="raw_access_action_denied",
                idempotency_key=idempotency_key,
                requesting_principal=requesting_principal,
                security_profile=security_profile,
                approval_ref=approval_ref,
                parameters=parameters,
            )
        if not action_kind or action_info is None or not action_info.get("enabled", False):
            return self._linked_result(
                link=link,
                action_kind=action_kind,
                risk=risk,
                decision="denied",
                reason="unsupported_action_kind",
                idempotency_key=idempotency_key,
                requesting_principal=requesting_principal,
                security_profile=security_profile,
                approval_ref=approval_ref,
                parameters=parameters,
            )
        if not permissions.can_view_target:
            return self._linked_result(
                link=link,
                action_kind=action_kind,
                risk=risk,
                decision="denied",
                reason="target_view_permission_required",
                idempotency_key=idempotency_key,
                requesting_principal=requesting_principal,
                security_profile=security_profile,
                approval_ref=approval_ref,
                parameters=parameters,
            )
        if authority_mode not in _SUPPORTED_AUTHORITY_MODES:
            return self._linked_result(
                link=link,
                action_kind=action_kind,
                risk=risk,
                decision="denied",
                reason="unsupported_authority_mode",
                idempotency_key=idempotency_key,
                requesting_principal=requesting_principal,
                security_profile=security_profile,
                approval_ref=approval_ref,
                parameters=parameters,
            )
        if dry_run:
            decision: RemediationActionDecision = (
                "dry_run_only" if authority_mode == "observe_only" else "allowed"
            )
            return self._linked_result(
                link=link,
                action_kind=action_kind,
                risk=risk,
                decision=decision,
                reason="dry_run",
                idempotency_key=idempotency_key,
                requesting_principal=requesting_principal,
                security_profile=security_profile,
                approval_ref=approval_ref,
                parameters=parameters,
            )
        if authority_mode == "observe_only":
            return self._linked_result(
                link=link,
                action_kind=action_kind,
                risk=risk,
                decision="denied",
                reason="observe_only_rejects_side_effects",
                idempotency_key=idempotency_key,
                requesting_principal=requesting_principal,
                security_profile=security_profile,
                approval_ref=approval_ref,
                parameters=parameters,
            )

        profile_error = _security_profile_error(
            security_profile=security_profile,
            permissions=permissions,
            action_kind=action_kind,
        )
        if profile_error is not None:
            return self._linked_result(
                link=link,
                action_kind=action_kind,
                risk=risk,
                decision="denied",
                reason=profile_error,
                idempotency_key=idempotency_key,
                requesting_principal=requesting_principal,
                security_profile=security_profile,
                approval_ref=approval_ref,
                parameters=parameters,
            )

        if authority_mode == "approval_gated" and not approval_ref:
            return self._linked_result(
                link=link,
                action_kind=action_kind,
                risk=risk,
                decision="approval_required",
                reason="approval_gated_requires_approval",
                idempotency_key=idempotency_key,
                requesting_principal=requesting_principal,
                security_profile=security_profile,
                approval_ref=approval_ref,
                parameters=parameters,
            )
        if risk == "high" and not approval_ref:
            return self._linked_result(
                link=link,
                action_kind=action_kind,
                risk=risk,
                decision="approval_required",
                reason="high_risk_requires_approval",
                idempotency_key=idempotency_key,
                requesting_principal=requesting_principal,
                security_profile=security_profile,
                approval_ref=approval_ref,
                parameters=parameters,
            )
        if approval_ref and risk == "high" and not permissions.can_approve_high_risk:
            return self._linked_result(
                link=link,
                action_kind=action_kind,
                risk=risk,
                decision="denied",
                reason="high_risk_approval_permission_required",
                idempotency_key=idempotency_key,
                requesting_principal=requesting_principal,
                security_profile=security_profile,
                approval_ref=approval_ref,
                parameters=parameters,
            )

        auto_allowed_risk = _DEFAULT_AUTO_ALLOWED_RISK
        if authority_mode == "admin_auto" and not approval_ref:
            if (
                risk is not None
                and _RISK_ORDER[str(risk)] > _RISK_ORDER[auto_allowed_risk]
            ):
                return self._linked_result(
                    link=link,
                    action_kind=action_kind,
                    risk=risk,
                    decision="approval_required",
                    reason="risk_exceeds_auto_policy",
                    idempotency_key=idempotency_key,
                    requesting_principal=requesting_principal,
                    security_profile=security_profile,
                    approval_ref=approval_ref,
                    parameters=parameters,
                )

        return self._linked_result(
            link=link,
            action_kind=action_kind,
            risk=risk,
            decision="allowed",
            reason="allowed",
            idempotency_key=idempotency_key,
            requesting_principal=requesting_principal,
            security_profile=security_profile,
            approval_ref=approval_ref,
            parameters=parameters,
        )

    def _linked_result(
        self,
        *,
        link: db_models.TemporalExecutionRemediationLink,
        action_kind: str,
        risk: RemediationActionRisk | None,
        decision: RemediationActionDecision,
        reason: str,
        idempotency_key: str,
        requesting_principal: str,
        security_profile: RemediationSecurityProfile | None,
        approval_ref: str | None,
        parameters: Mapping[str, Any] | None,
    ) -> RemediationActionAuthorityResult:
        return self._result(
            remediation_workflow_id=link.remediation_workflow_id,
            target_workflow_id=link.target_workflow_id,
            authority_mode=link.authority_mode,
            action_kind=action_kind,
            risk=risk,
            decision=decision,
            reason=reason,
            idempotency_key=idempotency_key,
            requesting_principal=requesting_principal,
            security_profile=security_profile,
            approval_ref=approval_ref,
            parameters=parameters,
        )

    @staticmethod
    def _result(
        *,
        remediation_workflow_id: str,
        target_workflow_id: str | None,
        authority_mode: str | None,
        action_kind: str,
        risk: RemediationActionRisk | None,
        decision: RemediationActionDecision,
        reason: str,
        idempotency_key: str,
        requesting_principal: str,
        security_profile: RemediationSecurityProfile | None,
        approval_ref: str | None,
        parameters: Mapping[str, Any] | None,
        summary: str | None = None,
    ) -> RemediationActionAuthorityResult:
        redacted_parameters = _redact_payload(parameters or {})
        execution_principal = (
            security_profile.execution_principal if security_profile else None
        )
        audit = {
            "requestingPrincipal": _redact_text(requesting_principal),
            "executionPrincipal": _redact_text(execution_principal),
            "decision": decision,
            "reason": reason,
            "summary": _redact_text(
                summary or f"{action_kind or 'action'} decision: {reason}"
            ),
        }
        return RemediationActionAuthorityResult(
            remediation_workflow_id=remediation_workflow_id,
            target_workflow_id=target_workflow_id,
            authority_mode=authority_mode,
            action_kind=action_kind,
            risk=risk,
            decision=decision,
            reason=reason,
            idempotency_key=idempotency_key,
            security_profile_ref=security_profile.profile_ref
            if security_profile
            else None,
            approval_ref=approval_ref,
            executable=decision == "allowed",
            redacted_parameters=redacted_parameters,
            audit=audit,
        )


def _security_profile_error(
    *,
    security_profile: RemediationSecurityProfile | None,
    permissions: RemediationPermissionSet,
    action_kind: str,
) -> str | None:
    if not permissions.can_request_admin_profile:
        return "admin_profile_permission_required"
    if security_profile is None:
        return "security_profile_required"
    if not security_profile.enabled:
        return "security_profile_disabled"
    if action_kind not in set(security_profile.allowed_action_kinds):
        return "security_profile_action_not_allowed"
    return None


def _redact_payload(value: Mapping[str, Any]) -> Mapping[str, Any]:
    redacted = redact_sensitive_payload(value)
    if isinstance(redacted, Mapping):
        return _scrub_paths(redacted)
    return {}


def _scrub_paths(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Mapping):
        return {str(key): _scrub_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub_paths(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_paths(item) for item in value)
    return value


def _redact_text(value: str | None) -> str:
    redacted = redact_sensitive_text(value)
    if redacted is None:
        return ""
    redacted = _PRESIGNED_URL_PATTERN.sub("[REDACTED_URL]", redacted)
    redacted = _ABSOLUTE_PATH_PATTERN.sub("[REDACTED_PATH]", redacted)
    return redacted


def _target_type(action_kind: str) -> str | None:
    action_info = _ACTION_CATALOG.get(action_kind)
    if action_info is None:
        return None
    return str(action_info.get("target_type") or "")


def _verification_hint(action_kind: str) -> str | None:
    action_info = _ACTION_CATALOG.get(action_kind)
    if action_info is None:
        return None
    return str(action_info.get("verification_hint") or "")


def _result_status(decision: RemediationActionDecision, reason: str) -> str:
    if reason == "dry_run":
        return "no_op"
    if decision == "allowed":
        return "applied"
    if decision == "approval_required":
        return "approval_required"
    if reason in {
        "target_view_permission_required",
        "security_profile_action_not_allowed",
        "unsupported_authority_mode",
    }:
        return "precondition_failed"
    if decision in {"denied", "dry_run_only"}:
        return "rejected"
    return "failed"


__all__ = [
    "RemediationActionAuthorityResult",
    "RemediationActionAuthorityService",
    "RemediationActionDecision",
    "RemediationActionRisk",
    "RemediationPermissionSet",
    "RemediationSecurityProfile",
]
