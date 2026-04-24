"""Remediation action authority decisions.

This module intentionally evaluates whether a remediation action may proceed; it
does not execute host, container, SQL, provider, or storage operations.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_service.db import models as db_models
from moonmind.utils.logging import redact_sensitive_payload, redact_sensitive_text

_GUARD_STATE_RETENTION_SECONDS = 24 * 60 * 60
_MAX_GUARD_STATE_ENTRIES = 2048

RemediationActionRisk = Literal["low", "medium", "high"]
RemediationActionDecision = Literal[
    "allowed",
    "approval_required",
    "dry_run_only",
    "denied",
]
RemediationMutationGuardDecision = Literal[
    "allowed",
    "denied",
    "no_op",
    "rediagnose",
    "escalate",
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
_COMMON_REASON_INPUT = {
    "reason": {"type": "string", "required": False},
}
_COMMON_AUDIT_PAYLOAD_SHAPE = {
    "actor": "string",
    "executionPrincipal": "string",
    "remediationWorkflowId": "string",
    "targetWorkflowId": "string",
    "actionKind": "string",
    "riskTier": "string",
    "decision": "string",
    "timestamp": "string",
}
_ACTION_CATALOG: dict[str, dict[str, Any]] = {
    "execution.pause": {
        "risk": "medium",
        "enabled": True,
        "target_type": "execution",
        "input_metadata": _COMMON_REASON_INPUT,
        "preconditions": ("target_visible", "target_active"),
        "idempotency": "same target/action/reason key returns the prior decision",
        "verification_hint": "verify target execution enters a paused or non-progressing state",
    },
    "execution.resume": {
        "risk": "medium",
        "enabled": True,
        "target_type": "execution",
        "input_metadata": _COMMON_REASON_INPUT,
        "preconditions": ("target_visible", "target_paused"),
        "idempotency": "same target/action/reason key returns the prior decision",
        "verification_hint": "verify target execution leaves paused state or reports no-op",
    },
    "execution.request_rerun_same_workflow": {
        "risk": "medium",
        "enabled": True,
        "target_type": "execution",
        "input_metadata": _COMMON_REASON_INPUT,
        "preconditions": ("target_visible", "rerun_supported"),
        "idempotency": "same target/action/reason key returns the prior decision",
        "verification_hint": "verify target run identity changes or no-op reason is recorded",
    },
    "execution.start_fresh_rerun": {
        "risk": "medium",
        "enabled": True,
        "target_type": "execution",
        "input_metadata": _COMMON_REASON_INPUT,
        "preconditions": ("target_visible", "fresh_rerun_allowed"),
        "idempotency": "same target/action/reason key returns the prior decision",
        "verification_hint": "verify a fresh execution is created and linked to the target",
    },
    "execution.cancel": {
        "risk": "medium",
        "enabled": True,
        "target_type": "execution",
        "input_metadata": _COMMON_REASON_INPUT,
        "preconditions": ("target_visible", "target_cancelable"),
        "idempotency": "same target/action/reason key returns the prior decision",
        "verification_hint": (
            "verify target execution records cancellation or a no-op terminal state"
        ),
    },
    "execution.force_terminate": {
        "risk": "high",
        "enabled": True,
        "target_type": "execution",
        "input_metadata": _COMMON_REASON_INPUT,
        "preconditions": ("target_visible", "force_termination_approved"),
        "idempotency": "same target/action/reason key returns the prior decision",
        "verification_hint": "verify target execution termination and high-risk audit evidence",
    },
    "session.interrupt_turn": {
        "risk": "medium",
        "enabled": True,
        "target_type": "managed_session",
        "input_metadata": _COMMON_REASON_INPUT,
        "preconditions": ("target_visible", "active_managed_turn"),
        "idempotency": "same target/action/reason key returns the prior decision",
        "verification_hint": (
            "verify active managed turn is interrupted and control artifact is produced"
        ),
    },
    "session.clear": {
        "risk": "medium",
        "enabled": True,
        "target_type": "managed_session",
        "input_metadata": _COMMON_REASON_INPUT,
        "preconditions": ("target_visible", "session_clear_supported"),
        "idempotency": "same target/action/reason key returns the prior decision",
        "verification_hint": "verify session clear boundary and continuity artifact are produced",
    },
    "session.cancel": {
        "risk": "medium",
        "enabled": True,
        "target_type": "managed_session",
        "input_metadata": _COMMON_REASON_INPUT,
        "preconditions": ("target_visible", "session_cancelable"),
        "idempotency": "same target/action/reason key returns the prior decision",
        "verification_hint": "verify session cancellation state and target run status",
    },
    "session.terminate": {
        "risk": "high",
        "enabled": True,
        "target_type": "managed_session",
        "input_metadata": _COMMON_REASON_INPUT,
        "preconditions": ("target_visible", "session_termination_approved"),
        "idempotency": "same target/action/reason key returns the prior decision",
        "verification_hint": "verify session termination state and target run status",
    },
    "session.restart_container": {
        "risk": "high",
        "enabled": True,
        "target_type": "managed_session",
        "input_metadata": _COMMON_REASON_INPUT,
        "preconditions": ("target_visible", "restart_approved", "owning_session_plane"),
        "idempotency": "same target/action/reason key returns the prior decision",
        "verification_hint": "verify new session identity and continuity boundary artifact",
    },
    "provider_profile.evict_stale_lease": {
        "risk": "medium",
        "enabled": True,
        "target_type": "provider_profile_lease",
        "input_metadata": {
            **_COMMON_REASON_INPUT,
            "profileRef": {"type": "string", "required": False},
        },
        "preconditions": ("target_visible", "lease_stale_or_orphaned"),
        "idempotency": "same target/action/profile key returns the prior decision",
        "verification_hint": "verify lease age, owner liveness, and slot state afterward",
    },
    "workload.restart_helper_container": {
        "risk": "medium",
        "enabled": True,
        "target_type": "workload_container",
        "input_metadata": _COMMON_REASON_INPUT,
        "preconditions": ("target_visible", "helper_container_owned_by_moonmind"),
        "idempotency": "same target/action/container key returns the prior decision",
        "verification_hint": "verify helper container health and target state",
    },
    "workload.reap_orphan_container": {
        "risk": "medium",
        "enabled": True,
        "target_type": "workload_container",
        "input_metadata": _COMMON_REASON_INPUT,
        "preconditions": ("target_visible", "container_orphaned_by_policy"),
        "idempotency": "same target/action/container key returns the prior decision",
        "verification_hint": "verify orphan container is gone or no-op reason is recorded",
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
                "verificationHint": (
                    verification_hint if verification_required else None
                ),
                "sideEffects": [],
            },
            "audit": dict(self.audit),
        }

@dataclass(frozen=True, slots=True)
class RemediationMutationGuardPolicy:
    """Policy inputs for side-effecting remediation mutation guards."""

    lock_scope: str = "target_execution"
    lock_mode: str = "exclusive"
    lock_ttl_seconds: int = 1800
    max_actions_per_target: int = 3
    max_attempts_per_action_kind: int = 2
    cooldown_seconds: int = 300
    allow_nested_remediation: bool = False
    allow_self_target: bool = False
    max_self_healing_depth: int = 1
    target_change_policy: Literal["no_op", "rediagnose", "escalate"] = "escalate"

@dataclass(frozen=True, slots=True)
class RemediationMutationLockDecision:
    status: str
    lock_id: str | None
    scope: str
    mode: str
    holder_workflow_id: str
    holder_run_id: str | None
    target_workflow_id: str
    target_run_id: str
    created_at: datetime | None
    expires_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "lockId": self.lock_id,
            "scope": self.scope,
            "mode": self.mode,
            "holderWorkflowId": self.holder_workflow_id,
            "holderRunId": self.holder_run_id,
            "targetWorkflowId": self.target_workflow_id,
            "targetRunId": self.target_run_id,
            "createdAt": _datetime_to_json(self.created_at),
            "expiresAt": _datetime_to_json(self.expires_at),
        }

@dataclass(frozen=True, slots=True)
class RemediationActionLedgerDecision:
    status: str
    duplicate: bool = False
    unsafe_reuse: bool = False
    request_shape_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "duplicate": self.duplicate,
            "unsafeReuse": self.unsafe_reuse,
            "requestShapeHash": self.request_shape_hash,
        }

@dataclass(frozen=True, slots=True)
class RemediationActionBudgetDecision:
    status: str
    actions_used: int
    max_actions_per_target: int
    attempts_for_action_kind: int
    max_attempts_per_action_kind: int
    cooldown_seconds: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "actionsUsed": self.actions_used,
            "maxActionsPerTarget": self.max_actions_per_target,
            "attemptsForActionKind": self.attempts_for_action_kind,
            "maxAttemptsPerActionKind": self.max_attempts_per_action_kind,
            "cooldownSeconds": self.cooldown_seconds,
        }

@dataclass(frozen=True, slots=True)
class RemediationNestedDecision:
    status: str
    allow_nested_remediation: bool
    allow_self_target: bool
    max_self_healing_depth: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "allowNestedRemediation": self.allow_nested_remediation,
            "allowSelfTarget": self.allow_self_target,
            "maxSelfHealingDepth": self.max_self_healing_depth,
        }

@dataclass(frozen=True, slots=True)
class RemediationTargetFreshnessDecision:
    status: str
    pinned_run_id: str | None
    current_run_id: str | None
    state: str | None
    summary: str | None
    session_identity: str | None
    target_run_changed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "pinnedRunId": self.pinned_run_id,
            "currentRunId": self.current_run_id,
            "state": self.state,
            "summary": _redact_text(self.summary),
            "sessionIdentity": _redact_text(self.session_identity),
            "targetRunChanged": self.target_run_changed,
        }

@dataclass(frozen=True, slots=True)
class RemediationMutationGuardResult:
    remediation_workflow_id: str
    target_workflow_id: str
    action_kind: str
    idempotency_key: str
    decision: RemediationMutationGuardDecision
    reason: str
    executable: bool
    lock: RemediationMutationLockDecision
    ledger: RemediationActionLedgerDecision
    budget: RemediationActionBudgetDecision
    nested_remediation: RemediationNestedDecision
    target_freshness: RemediationTargetFreshnessDecision
    redacted_parameters: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "v1",
            "decision": self.decision,
            "reason": self.reason,
            "executable": self.executable,
            "remediationWorkflowId": self.remediation_workflow_id,
            "targetWorkflowId": self.target_workflow_id,
            "actionKind": self.action_kind,
            "idempotencyKey": self.idempotency_key,
            "lock": self.lock.to_dict(),
            "ledger": self.ledger.to_dict(),
            "budget": self.budget.to_dict(),
            "nestedRemediation": self.nested_remediation.to_dict(),
            "targetFreshness": self.target_freshness.to_dict(),
            "redactedParameters": dict(self.redacted_parameters),
        }

@dataclass(slots=True)
class _ActiveMutationLock:
    lock_id: str
    scope: str
    mode: str
    holder_workflow_id: str
    holder_run_id: str | None
    target_workflow_id: str
    target_run_id: str
    created_at: datetime
    expires_at: datetime
    released: bool = False

@dataclass(frozen=True, slots=True)
class _LedgerEntry:
    request_shape_hash: str
    recorded_at: datetime
    result: RemediationMutationGuardResult
    durable: bool = False

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
                    "preconditions": tuple(action_info.get("preconditions") or ()),
                    "idempotency": action_info.get("idempotency"),
                    "verificationRequired": True,
                    "verificationHint": action_info["verification_hint"],
                    "auditPayloadShape": deepcopy(_COMMON_AUDIT_PAYLOAD_SHAPE),
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
        if (
            not action_kind
            or action_info is None
            or not action_info.get("enabled", False)
        ):
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

class RemediationMutationGuardService:
    """Evaluate mutation guard preconditions before side-effecting actions."""

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session
        self._locks: dict[tuple[str, str, str], _ActiveMutationLock] = {}
        self._locks_by_id: dict[str, _ActiveMutationLock] = {}
        self._lost_holders: dict[tuple[str, str, str, str], datetime] = {}
        self._ledger: dict[tuple[str, str], _LedgerEntry] = {}
        self._action_counts_by_target: defaultdict[str, int] = defaultdict(int)
        self._attempts_by_target_action: defaultdict[tuple[str, str], int] = (
            defaultdict(int)
        )
        self._last_attempt_by_shape: dict[tuple[str, str, str], datetime] = {}
        self._last_target_activity: dict[str, datetime] = {}

    async def release_lock(self, lock_id: str) -> None:
        """Mark a lock as lost/released so the holder cannot silently continue."""

        lock = self._locks_by_id.get(str(lock_id or "").strip())
        if lock is None:
            return
        lock.released = True
        self._lost_holders[
            (
                lock.scope,
                lock.target_workflow_id,
                lock.target_run_id,
                lock.holder_workflow_id,
            )
        ] = lock.expires_at
        await self._persist_lock_release(lock)

    async def evaluate(
        self,
        *,
        remediation_workflow_id: str,
        remediation_run_id: str | None,
        target_workflow_id: str,
        target_run_id: str,
        action_kind: str,
        idempotency_key: str,
        parameters: Mapping[str, Any] | None,
        policy: RemediationMutationGuardPolicy | None = None,
        now: datetime,
        target_freshness: Mapping[str, Any] | Any | None = None,
        require_target_freshness: bool = False,
        target_is_remediation: bool = False,
        self_healing_depth: int = 1,
    ) -> RemediationMutationGuardResult:
        active_policy = _normalize_guard_policy(policy)
        current_time = _normalize_datetime(now)
        workflow_id = str(remediation_workflow_id or "").strip()
        target_id = str(target_workflow_id or "").strip()
        pinned_run_id = str(target_run_id or "").strip()
        normalized_action = str(action_kind or "").strip()
        idem = str(idempotency_key or "").strip()
        await self._hydrate_durable_state(
            remediation_workflow_id=workflow_id,
            target_workflow_id=target_id,
            target_run_id=pinned_run_id,
            now=current_time,
        )
        self._cleanup_state(now=current_time)
        redacted_parameters = _redact_payload(parameters or {})
        shape_hash = _request_shape_hash(
            action_kind=normalized_action,
            target_workflow_id=target_id,
            target_run_id=pinned_run_id,
            parameters=parameters or {},
        )
        base_lock = RemediationMutationLockDecision(
            status="not_evaluated",
            lock_id=None,
            scope=active_policy.lock_scope,
            mode=active_policy.lock_mode,
            holder_workflow_id=workflow_id,
            holder_run_id=remediation_run_id,
            target_workflow_id=target_id,
            target_run_id=pinned_run_id,
            created_at=None,
            expires_at=None,
        )
        base_budget = RemediationActionBudgetDecision(
            status="not_evaluated",
            actions_used=self._action_counts_by_target[target_id],
            max_actions_per_target=active_policy.max_actions_per_target,
            attempts_for_action_kind=self._attempts_by_target_action[
                (target_id, normalized_action)
            ],
            max_attempts_per_action_kind=active_policy.max_attempts_per_action_kind,
            cooldown_seconds=active_policy.cooldown_seconds,
        )
        nested_decision = _nested_decision(
            remediation_workflow_id=workflow_id,
            target_workflow_id=target_id,
            target_is_remediation=target_is_remediation,
            self_healing_depth=self_healing_depth,
            policy=active_policy,
        )
        freshness_decision = _freshness_decision(
            target_freshness=target_freshness,
            target_run_id=pinned_run_id,
            require_target_freshness=require_target_freshness,
        )

        if not idem:
            return self._guard_result(
                remediation_workflow_id=workflow_id,
                target_workflow_id=target_id,
                action_kind=normalized_action,
                idempotency_key=idem,
                decision="denied",
                reason="idempotency_key_required",
                executable=False,
                lock=base_lock,
                ledger=RemediationActionLedgerDecision(
                    status="missing_key",
                    request_shape_hash=shape_hash,
                ),
                budget=base_budget,
                nested=nested_decision,
                freshness=freshness_decision,
                redacted_parameters=redacted_parameters,
            )

        ledger_key = (workflow_id, idem)
        existing_ledger = self._ledger.get(ledger_key)
        if existing_ledger is not None:
            if existing_ledger.request_shape_hash == shape_hash:
                if existing_ledger.durable:
                    return _duplicate_guard_result(existing_ledger.result)
                return existing_ledger.result
            return self._guard_result(
                remediation_workflow_id=workflow_id,
                target_workflow_id=target_id,
                action_kind=normalized_action,
                idempotency_key=idem,
                decision="denied",
                reason="idempotency_key_unsafe_reuse",
                executable=False,
                lock=base_lock,
                ledger=RemediationActionLedgerDecision(
                    status="unsafe_reuse",
                    unsafe_reuse=True,
                    request_shape_hash=shape_hash,
                ),
                budget=base_budget,
                nested=nested_decision,
                freshness=freshness_decision,
                redacted_parameters=redacted_parameters,
            )

        raw_access = (
            normalized_action in _RAW_ACCESS_ACTION_KINDS
            or normalized_action.startswith("raw_")
        )
        if raw_access:
            return self._guard_result(
                remediation_workflow_id=workflow_id,
                target_workflow_id=target_id,
                action_kind=normalized_action,
                idempotency_key=idem,
                decision="denied",
                reason="raw_access_action_denied",
                executable=False,
                lock=base_lock,
                ledger=RemediationActionLedgerDecision(
                    status="not_recorded",
                    request_shape_hash=shape_hash,
                ),
                budget=base_budget,
                nested=nested_decision,
                freshness=freshness_decision,
                redacted_parameters=redacted_parameters,
            )

        if nested_decision.status != "allowed":
            reason = {
                "self_target_denied": "self_target_denied",
                "nested_remediation_denied": "nested_remediation_denied",
                "self_healing_depth_exceeded": "self_healing_depth_exceeded",
            }.get(nested_decision.status, "nested_remediation_denied")
            return self._guard_result(
                remediation_workflow_id=workflow_id,
                target_workflow_id=target_id,
                action_kind=normalized_action,
                idempotency_key=idem,
                decision="denied",
                reason=reason,
                executable=False,
                lock=base_lock,
                ledger=RemediationActionLedgerDecision(
                    status="not_recorded",
                    request_shape_hash=shape_hash,
                ),
                budget=base_budget,
                nested=nested_decision,
                freshness=freshness_decision,
                redacted_parameters=redacted_parameters,
            )

        lock_decision, lock_allows = await self._acquire_lock(
            workflow_id=workflow_id,
            remediation_run_id=remediation_run_id,
            target_workflow_id=target_id,
            target_run_id=pinned_run_id,
            policy=active_policy,
            now=current_time,
        )
        if not lock_allows:
            return self._guard_result(
                remediation_workflow_id=workflow_id,
                target_workflow_id=target_id,
                action_kind=normalized_action,
                idempotency_key=idem,
                decision="denied",
                reason=lock_decision.status,
                executable=False,
                lock=lock_decision,
                ledger=RemediationActionLedgerDecision(
                    status="not_recorded",
                    request_shape_hash=shape_hash,
                ),
                budget=base_budget,
                nested=nested_decision,
                freshness=freshness_decision,
                redacted_parameters=redacted_parameters,
            )

        if freshness_decision.status == "unavailable":
            return await self._record_guard_result(
                ledger_key=ledger_key,
                shape_hash=shape_hash,
                result=self._guard_result(
                    remediation_workflow_id=workflow_id,
                    target_workflow_id=target_id,
                    action_kind=normalized_action,
                    idempotency_key=idem,
                    decision="denied",
                    reason="target_health_unavailable",
                    executable=False,
                    lock=lock_decision,
                    ledger=RemediationActionLedgerDecision(
                        status="recorded",
                        request_shape_hash=shape_hash,
                    ),
                    budget=base_budget,
                    nested=nested_decision,
                    freshness=freshness_decision,
                    redacted_parameters=redacted_parameters,
                ),
            )
        if freshness_decision.status == "materially_changed":
            decision: RemediationMutationGuardDecision = (
                active_policy.target_change_policy
            )
            return await self._record_guard_result(
                ledger_key=ledger_key,
                shape_hash=shape_hash,
                result=self._guard_result(
                    remediation_workflow_id=workflow_id,
                    target_workflow_id=target_id,
                    action_kind=normalized_action,
                    idempotency_key=idem,
                    decision=decision,
                    reason="target_materially_changed",
                    executable=False,
                    lock=lock_decision,
                    ledger=RemediationActionLedgerDecision(
                        status="recorded",
                        request_shape_hash=shape_hash,
                    ),
                    budget=base_budget,
                    nested=nested_decision,
                    freshness=freshness_decision,
                    redacted_parameters=redacted_parameters,
                ),
            )

        budget_decision, budget_reason = self._evaluate_budget(
            target_workflow_id=target_id,
            action_kind=normalized_action,
            shape_hash=shape_hash,
            policy=active_policy,
            now=current_time,
        )
        if budget_reason is not None:
            decision = (
                "escalate"
                if budget_reason
                in {"action_budget_exhausted", "action_kind_attempt_budget_exhausted"}
                else "denied"
            )
            return self._guard_result(
                remediation_workflow_id=workflow_id,
                target_workflow_id=target_id,
                action_kind=normalized_action,
                idempotency_key=idem,
                decision=decision,
                reason=budget_reason,
                executable=False,
                lock=lock_decision,
                ledger=RemediationActionLedgerDecision(
                    status="not_recorded",
                    request_shape_hash=shape_hash,
                ),
                budget=budget_decision,
                nested=nested_decision,
                freshness=freshness_decision,
                redacted_parameters=redacted_parameters,
            )

        self._action_counts_by_target[target_id] += 1
        self._attempts_by_target_action[(target_id, normalized_action)] += 1
        self._last_target_activity[target_id] = current_time
        self._last_attempt_by_shape[(target_id, normalized_action, shape_hash)] = (
            current_time
        )
        accepted_budget = RemediationActionBudgetDecision(
            status="within_budget",
            actions_used=self._action_counts_by_target[target_id],
            max_actions_per_target=active_policy.max_actions_per_target,
            attempts_for_action_kind=self._attempts_by_target_action[
                (target_id, normalized_action)
            ],
            max_attempts_per_action_kind=active_policy.max_attempts_per_action_kind,
            cooldown_seconds=active_policy.cooldown_seconds,
        )
        return await self._record_guard_result(
            ledger_key=ledger_key,
            shape_hash=shape_hash,
            result=self._guard_result(
                remediation_workflow_id=workflow_id,
                target_workflow_id=target_id,
                action_kind=normalized_action,
                idempotency_key=idem,
                decision="allowed",
                reason="allowed",
                executable=True,
                lock=lock_decision,
                ledger=RemediationActionLedgerDecision(
                    status="recorded",
                    request_shape_hash=shape_hash,
                ),
                budget=accepted_budget,
                nested=nested_decision,
                freshness=freshness_decision,
                redacted_parameters=redacted_parameters,
            ),
        )

    async def _acquire_lock(
        self,
        *,
        workflow_id: str,
        remediation_run_id: str | None,
        target_workflow_id: str,
        target_run_id: str,
        policy: RemediationMutationGuardPolicy,
        now: datetime,
    ) -> tuple[RemediationMutationLockDecision, bool]:
        lock_key = (policy.lock_scope, target_workflow_id, target_run_id)
        lost_key = (*lock_key, workflow_id)
        if lost_key in self._lost_holders:
            return (
                RemediationMutationLockDecision(
                    status="mutation_lock_lost",
                    lock_id=None,
                    scope=policy.lock_scope,
                    mode=policy.lock_mode,
                    holder_workflow_id=workflow_id,
                    holder_run_id=remediation_run_id,
                    target_workflow_id=target_workflow_id,
                    target_run_id=target_run_id,
                    created_at=None,
                    expires_at=None,
                ),
                False,
            )

        existing = self._locks.get(lock_key)
        expired_existing = False
        if existing is not None and existing.released:
            existing = None
        if existing is not None and now >= existing.expires_at:
            expired_existing = True
            existing = None
        if existing is not None and existing.holder_workflow_id == workflow_id:
            return (_lock_decision(existing, "acquired"), True)
        if existing is not None and now < existing.expires_at:
            return (_lock_decision(existing, "mutation_lock_conflict"), False)

        status = "recovered" if expired_existing else "acquired"
        lock = _ActiveMutationLock(
            lock_id=_stable_lock_id(
                policy.lock_scope,
                target_workflow_id,
                target_run_id,
                workflow_id,
            ),
            scope=policy.lock_scope,
            mode=policy.lock_mode,
            holder_workflow_id=workflow_id,
            holder_run_id=remediation_run_id,
            target_workflow_id=target_workflow_id,
            target_run_id=target_run_id,
            created_at=now,
            expires_at=now + timedelta(seconds=max(policy.lock_ttl_seconds, 1)),
        )
        self._locks[lock_key] = lock
        self._locks_by_id[lock.lock_id] = lock
        await self._persist_lock(lock)
        return (_lock_decision(lock, status), True)

    def _evaluate_budget(
        self,
        *,
        target_workflow_id: str,
        action_kind: str,
        shape_hash: str,
        policy: RemediationMutationGuardPolicy,
        now: datetime,
    ) -> tuple[RemediationActionBudgetDecision, str | None]:
        actions_used = self._action_counts_by_target[target_workflow_id]
        attempts = self._attempts_by_target_action[(target_workflow_id, action_kind)]
        base = RemediationActionBudgetDecision(
            status="within_budget",
            actions_used=actions_used,
            max_actions_per_target=policy.max_actions_per_target,
            attempts_for_action_kind=attempts,
            max_attempts_per_action_kind=policy.max_attempts_per_action_kind,
            cooldown_seconds=policy.cooldown_seconds,
        )
        if actions_used >= policy.max_actions_per_target:
            return (
                RemediationActionBudgetDecision(
                    status="action_budget_exhausted",
                    actions_used=actions_used,
                    max_actions_per_target=policy.max_actions_per_target,
                    attempts_for_action_kind=attempts,
                    max_attempts_per_action_kind=policy.max_attempts_per_action_kind,
                    cooldown_seconds=policy.cooldown_seconds,
                ),
                "action_budget_exhausted",
            )
        if attempts >= policy.max_attempts_per_action_kind:
            return (
                RemediationActionBudgetDecision(
                    status="action_kind_attempt_budget_exhausted",
                    actions_used=actions_used,
                    max_actions_per_target=policy.max_actions_per_target,
                    attempts_for_action_kind=attempts,
                    max_attempts_per_action_kind=policy.max_attempts_per_action_kind,
                    cooldown_seconds=policy.cooldown_seconds,
                ),
                "action_kind_attempt_budget_exhausted",
            )
        last_attempt = self._last_attempt_by_shape.get(
            (target_workflow_id, action_kind, shape_hash)
        )
        if (
            last_attempt is not None
            and policy.cooldown_seconds > 0
            and now < last_attempt + timedelta(seconds=policy.cooldown_seconds)
        ):
            return (
                RemediationActionBudgetDecision(
                    status="action_cooldown_active",
                    actions_used=actions_used,
                    max_actions_per_target=policy.max_actions_per_target,
                    attempts_for_action_kind=attempts,
                    max_attempts_per_action_kind=policy.max_attempts_per_action_kind,
                    cooldown_seconds=policy.cooldown_seconds,
                ),
                "action_cooldown_active",
            )
        return (base, None)

    @staticmethod
    def _guard_result(
        *,
        remediation_workflow_id: str,
        target_workflow_id: str,
        action_kind: str,
        idempotency_key: str,
        decision: RemediationMutationGuardDecision,
        reason: str,
        executable: bool,
        lock: RemediationMutationLockDecision,
        ledger: RemediationActionLedgerDecision,
        budget: RemediationActionBudgetDecision,
        nested: RemediationNestedDecision,
        freshness: RemediationTargetFreshnessDecision,
        redacted_parameters: Mapping[str, Any],
    ) -> RemediationMutationGuardResult:
        return RemediationMutationGuardResult(
            remediation_workflow_id=remediation_workflow_id,
            target_workflow_id=target_workflow_id,
            action_kind=action_kind,
            idempotency_key=idempotency_key,
            decision=decision,
            reason=reason,
            executable=executable,
            lock=lock,
            ledger=ledger,
            budget=budget,
            nested_remediation=nested,
            target_freshness=freshness,
            redacted_parameters=redacted_parameters,
        )

    async def _record_guard_result(
        self,
        *,
        ledger_key: tuple[str, str],
        shape_hash: str,
        result: RemediationMutationGuardResult,
    ) -> RemediationMutationGuardResult:
        self._ledger[ledger_key] = _LedgerEntry(
            request_shape_hash=shape_hash,
            recorded_at=result.lock.created_at
            or datetime.fromtimestamp(0, timezone.utc),
            result=result,
        )
        await self._persist_ledger_entry(
            remediation_workflow_id=result.remediation_workflow_id,
            idempotency_key=result.idempotency_key,
            shape_hash=shape_hash,
            result=result,
        )
        self._trim_state()
        return result

    async def _hydrate_durable_state(
        self,
        *,
        remediation_workflow_id: str,
        target_workflow_id: str,
        target_run_id: str,
        now: datetime,
    ) -> None:
        if remediation_workflow_id:
            link = await self._session.get(
                db_models.TemporalExecutionRemediationLink,
                remediation_workflow_id,
            )
            if link is not None:
                self._hydrate_ledger_from_link(link)

        if not target_workflow_id or not target_run_id:
            return

        result = await self._session.execute(
            select(db_models.TemporalExecutionRemediationLink).where(
                db_models.TemporalExecutionRemediationLink.target_workflow_id
                == target_workflow_id
            )
        )
        for link in result.scalars():
            lock = _active_lock_from_payload(
                getattr(link, "mutation_guard_lock_state", None),
            )
            if lock is None or lock.target_run_id != target_run_id:
                continue
            if lock.released:
                self._lost_holders[
                    (
                        lock.scope,
                        lock.target_workflow_id,
                        lock.target_run_id,
                        lock.holder_workflow_id,
                    )
                ] = lock.expires_at
                continue
            if now >= lock.expires_at:
                continue
            lock_key = (lock.scope, lock.target_workflow_id, lock.target_run_id)
            existing = self._locks.get(lock_key)
            if existing is None or existing.expires_at < lock.expires_at:
                self._locks[lock_key] = lock
                self._locks_by_id[lock.lock_id] = lock

    def _hydrate_ledger_from_link(
        self, link: db_models.TemporalExecutionRemediationLink
    ) -> None:
        payload = getattr(link, "mutation_guard_ledger_state", None)
        if not isinstance(payload, Mapping):
            return
        entries = payload.get("entries")
        if not isinstance(entries, Mapping):
            return
        workflow_id = str(getattr(link, "remediation_workflow_id", "") or "")
        for idempotency_key, entry in entries.items():
            if not isinstance(entry, Mapping):
                continue
            idem = str(idempotency_key or "").strip()
            shape_hash = str(entry.get("requestShapeHash") or "").strip()
            result_payload = entry.get("result")
            if not idem or not shape_hash or not isinstance(result_payload, Mapping):
                continue
            ledger_key = (workflow_id, idem)
            if ledger_key in self._ledger:
                continue
            result = _guard_result_from_payload(result_payload)
            if result is None:
                continue
            self._ledger[ledger_key] = _LedgerEntry(
                request_shape_hash=shape_hash,
                recorded_at=_datetime_from_json(entry.get("recordedAt"))
                or datetime.fromtimestamp(0, timezone.utc),
                result=result,
                durable=True,
            )

    async def _persist_lock(self, lock: _ActiveMutationLock) -> None:
        link = await self._session.get(
            db_models.TemporalExecutionRemediationLink,
            lock.holder_workflow_id,
        )
        if link is None:
            return
        link.active_lock_scope = lock.scope
        link.active_lock_holder = lock.holder_workflow_id
        link.mutation_guard_lock_state = _active_lock_payload(lock)
        await self._session.flush()

    async def _persist_lock_release(self, lock: _ActiveMutationLock) -> None:
        link = await self._session.get(
            db_models.TemporalExecutionRemediationLink,
            lock.holder_workflow_id,
        )
        if link is None:
            return
        link.active_lock_scope = None
        link.active_lock_holder = None
        link.mutation_guard_lock_state = _active_lock_payload(lock)
        await self._session.flush()

    async def _persist_ledger_entry(
        self,
        *,
        remediation_workflow_id: str,
        idempotency_key: str,
        shape_hash: str,
        result: RemediationMutationGuardResult,
    ) -> None:
        link = await self._session.get(
            db_models.TemporalExecutionRemediationLink,
            remediation_workflow_id,
        )
        if link is None:
            return
        state = dict(getattr(link, "mutation_guard_ledger_state", None) or {})
        entries = dict(state.get("entries") or {})
        entries[idempotency_key] = {
            "requestShapeHash": shape_hash,
            "recordedAt": _datetime_to_json(
                result.lock.created_at or datetime.now(timezone.utc)
            ),
            "result": result.to_dict(),
        }
        state["entries"] = entries
        link.mutation_guard_ledger_state = state
        link.latest_action_summary = result.action_kind
        link.outcome = result.decision
        await self._session.flush()

    def _cleanup_state(self, *, now: datetime) -> None:
        cutoff = now - timedelta(seconds=_GUARD_STATE_RETENTION_SECONDS)
        for key, lock in list(self._locks.items()):
            if lock.released or lock.expires_at <= cutoff:
                self._locks.pop(key, None)
                self._locks_by_id.pop(lock.lock_id, None)
        for key, entry in list(self._ledger.items()):
            if entry.recorded_at <= cutoff:
                self._ledger.pop(key, None)
        for key, recorded_at in list(self._lost_holders.items()):
            if recorded_at <= cutoff:
                self._lost_holders.pop(key, None)
        for key, last_attempt in list(self._last_attempt_by_shape.items()):
            if last_attempt <= cutoff:
                self._last_attempt_by_shape.pop(key, None)
        for target_id, last_activity in list(self._last_target_activity.items()):
            if last_activity > cutoff:
                continue
            self._last_target_activity.pop(target_id, None)
            self._action_counts_by_target.pop(target_id, None)
            for action_key in list(self._attempts_by_target_action):
                if action_key[0] == target_id:
                    self._attempts_by_target_action.pop(action_key, None)
        self._trim_state()

    def _trim_state(self) -> None:
        while len(self._ledger) > _MAX_GUARD_STATE_ENTRIES:
            oldest_key = min(
                self._ledger,
                key=lambda key: self._ledger[key].recorded_at,
            )
            self._ledger.pop(oldest_key, None)
        while len(self._last_attempt_by_shape) > _MAX_GUARD_STATE_ENTRIES:
            oldest_shape_key = min(
                self._last_attempt_by_shape,
                key=lambda key: self._last_attempt_by_shape[key],
            )
            self._last_attempt_by_shape.pop(oldest_shape_key, None)

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

def _normalize_guard_policy(
    policy: RemediationMutationGuardPolicy | None,
) -> RemediationMutationGuardPolicy:
    if policy is None:
        return RemediationMutationGuardPolicy()
    return RemediationMutationGuardPolicy(
        lock_scope=str(policy.lock_scope or "target_execution").strip()
        or "target_execution",
        lock_mode=str(policy.lock_mode or "exclusive").strip() or "exclusive",
        lock_ttl_seconds=max(int(policy.lock_ttl_seconds), 1),
        max_actions_per_target=max(int(policy.max_actions_per_target), 0),
        max_attempts_per_action_kind=max(int(policy.max_attempts_per_action_kind), 0),
        cooldown_seconds=max(int(policy.cooldown_seconds), 0),
        allow_nested_remediation=bool(policy.allow_nested_remediation),
        allow_self_target=bool(policy.allow_self_target),
        max_self_healing_depth=max(int(policy.max_self_healing_depth), 1),
        target_change_policy=policy.target_change_policy
        if policy.target_change_policy in {"no_op", "rediagnose", "escalate"}
        else "escalate",
    )

def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value

def _datetime_to_json(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _normalize_datetime(value).isoformat().replace("+00:00", "Z")

def _datetime_from_json(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _normalize_datetime(parsed)

def _request_shape_hash(
    *,
    action_kind: str,
    target_workflow_id: str,
    target_run_id: str,
    parameters: Mapping[str, Any],
) -> str:
    payload = {
        "actionKind": action_kind,
        "targetWorkflowId": target_workflow_id,
        "targetRunId": target_run_id,
        "parameters": parameters,
    }
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

def _stable_lock_id(*parts: str) -> str:
    encoded = "|".join(str(part) for part in parts)
    return "rlock_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]

def _lock_decision(
    lock: _ActiveMutationLock,
    status: str,
) -> RemediationMutationLockDecision:
    return RemediationMutationLockDecision(
        status=status,
        lock_id=lock.lock_id,
        scope=lock.scope,
        mode=lock.mode,
        holder_workflow_id=lock.holder_workflow_id,
        holder_run_id=lock.holder_run_id,
        target_workflow_id=lock.target_workflow_id,
        target_run_id=lock.target_run_id,
        created_at=lock.created_at,
        expires_at=lock.expires_at,
    )

def _active_lock_payload(lock: _ActiveMutationLock) -> dict[str, Any]:
    return {
        "lockId": lock.lock_id,
        "scope": lock.scope,
        "mode": lock.mode,
        "holderWorkflowId": lock.holder_workflow_id,
        "holderRunId": lock.holder_run_id,
        "targetWorkflowId": lock.target_workflow_id,
        "targetRunId": lock.target_run_id,
        "createdAt": _datetime_to_json(lock.created_at),
        "expiresAt": _datetime_to_json(lock.expires_at),
        "released": bool(lock.released),
    }

def _active_lock_from_payload(payload: Any) -> _ActiveMutationLock | None:
    if not isinstance(payload, Mapping):
        return None
    lock_id = str(payload.get("lockId") or "").strip()
    scope = str(payload.get("scope") or "").strip()
    mode = str(payload.get("mode") or "").strip()
    holder = str(payload.get("holderWorkflowId") or "").strip()
    target = str(payload.get("targetWorkflowId") or "").strip()
    target_run = str(payload.get("targetRunId") or "").strip()
    created_at = _datetime_from_json(payload.get("createdAt"))
    expires_at = _datetime_from_json(payload.get("expiresAt"))
    required = (lock_id, scope, mode, holder, target, target_run, created_at, expires_at)
    if not all(required):
        return None
    return _ActiveMutationLock(
        lock_id=lock_id,
        scope=scope,
        mode=mode,
        holder_workflow_id=holder,
        holder_run_id=_string_or_none(payload.get("holderRunId")),
        target_workflow_id=target,
        target_run_id=target_run,
        created_at=created_at,
        expires_at=expires_at,
        released=bool(payload.get("released")),
    )

def _duplicate_guard_result(
    result: RemediationMutationGuardResult,
) -> RemediationMutationGuardResult:
    return RemediationMutationGuardResult(
        remediation_workflow_id=result.remediation_workflow_id,
        target_workflow_id=result.target_workflow_id,
        action_kind=result.action_kind,
        idempotency_key=result.idempotency_key,
        decision=result.decision,
        reason=result.reason,
        executable=result.executable,
        lock=result.lock,
        ledger=RemediationActionLedgerDecision(
            status=result.ledger.status,
            duplicate=True,
            unsafe_reuse=result.ledger.unsafe_reuse,
            request_shape_hash=result.ledger.request_shape_hash,
        ),
        budget=result.budget,
        nested_remediation=result.nested_remediation,
        target_freshness=result.target_freshness,
        redacted_parameters=result.redacted_parameters,
    )

def _guard_result_from_payload(
    payload: Mapping[str, Any],
) -> RemediationMutationGuardResult | None:
    lock_payload = payload.get("lock")
    ledger_payload = payload.get("ledger")
    budget_payload = payload.get("budget")
    nested_payload = payload.get("nestedRemediation")
    freshness_payload = payload.get("targetFreshness")
    if not all(
        isinstance(item, Mapping)
        for item in (
            lock_payload,
            ledger_payload,
            budget_payload,
            nested_payload,
            freshness_payload,
        )
    ):
        return None

    lock = RemediationMutationLockDecision(
        status=str(lock_payload.get("status") or ""),
        lock_id=_string_or_none(lock_payload.get("lockId")),
        scope=str(lock_payload.get("scope") or ""),
        mode=str(lock_payload.get("mode") or ""),
        holder_workflow_id=str(lock_payload.get("holderWorkflowId") or ""),
        holder_run_id=_string_or_none(lock_payload.get("holderRunId")),
        target_workflow_id=str(lock_payload.get("targetWorkflowId") or ""),
        target_run_id=str(lock_payload.get("targetRunId") or ""),
        created_at=_datetime_from_json(lock_payload.get("createdAt")),
        expires_at=_datetime_from_json(lock_payload.get("expiresAt")),
    )
    ledger = RemediationActionLedgerDecision(
        status=str(ledger_payload.get("status") or ""),
        duplicate=bool(ledger_payload.get("duplicate")),
        unsafe_reuse=bool(ledger_payload.get("unsafeReuse")),
        request_shape_hash=_string_or_none(ledger_payload.get("requestShapeHash")),
    )
    budget = RemediationActionBudgetDecision(
        status=str(budget_payload.get("status") or ""),
        actions_used=_int_or_zero(budget_payload.get("actionsUsed")),
        max_actions_per_target=_int_or_zero(budget_payload.get("maxActionsPerTarget")),
        attempts_for_action_kind=_int_or_zero(
            budget_payload.get("attemptsForActionKind")
        ),
        max_attempts_per_action_kind=_int_or_zero(
            budget_payload.get("maxAttemptsPerActionKind")
        ),
        cooldown_seconds=_int_or_zero(budget_payload.get("cooldownSeconds")),
    )
    nested = RemediationNestedDecision(
        status=str(nested_payload.get("status") or ""),
        allow_nested_remediation=bool(nested_payload.get("allowNestedRemediation")),
        allow_self_target=bool(nested_payload.get("allowSelfTarget")),
        max_self_healing_depth=_int_or_zero(
            nested_payload.get("maxSelfHealingDepth")
        ),
    )
    freshness = RemediationTargetFreshnessDecision(
        status=str(freshness_payload.get("status") or ""),
        pinned_run_id=_string_or_none(freshness_payload.get("pinnedRunId")),
        current_run_id=_string_or_none(freshness_payload.get("currentRunId")),
        state=_string_or_none(freshness_payload.get("state")),
        summary=_string_or_none(freshness_payload.get("summary")),
        session_identity=_string_or_none(freshness_payload.get("sessionIdentity")),
        target_run_changed=bool(freshness_payload.get("targetRunChanged")),
    )
    redacted = payload.get("redactedParameters")
    return RemediationMutationGuardResult(
        remediation_workflow_id=str(payload.get("remediationWorkflowId") or ""),
        target_workflow_id=str(payload.get("targetWorkflowId") or ""),
        action_kind=str(payload.get("actionKind") or ""),
        idempotency_key=str(payload.get("idempotencyKey") or ""),
        decision=str(payload.get("decision") or "denied"),
        reason=str(payload.get("reason") or ""),
        executable=bool(payload.get("executable")),
        lock=lock,
        ledger=ledger,
        budget=budget,
        nested_remediation=nested,
        target_freshness=freshness,
        redacted_parameters=redacted if isinstance(redacted, Mapping) else {},
    )

def _string_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None

def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

def _nested_decision(
    *,
    remediation_workflow_id: str,
    target_workflow_id: str,
    target_is_remediation: bool,
    self_healing_depth: int,
    policy: RemediationMutationGuardPolicy,
) -> RemediationNestedDecision:
    status = "allowed"
    if (
        target_workflow_id == remediation_workflow_id
        and target_is_remediation
        and not policy.allow_self_target
    ):
        status = "self_target_denied"
    elif target_is_remediation and not policy.allow_nested_remediation:
        status = "nested_remediation_denied"
    elif self_healing_depth > policy.max_self_healing_depth:
        status = "self_healing_depth_exceeded"
    return RemediationNestedDecision(
        status=status,
        allow_nested_remediation=policy.allow_nested_remediation,
        allow_self_target=policy.allow_self_target,
        max_self_healing_depth=policy.max_self_healing_depth,
    )

def _freshness_decision(
    *,
    target_freshness: Mapping[str, Any] | Any | None,
    target_run_id: str,
    require_target_freshness: bool,
) -> RemediationTargetFreshnessDecision:
    if target_freshness is None:
        return RemediationTargetFreshnessDecision(
            status="unavailable" if require_target_freshness else "not_required",
            pinned_run_id=target_run_id,
            current_run_id=None,
            state=None,
            summary=None,
            session_identity=None,
            target_run_changed=False,
        )
    pinned_run_id = _freshness_value(target_freshness, "pinnedRunId", "pinned_run_id")
    current_run_id = _freshness_value(
        target_freshness,
        "currentRunId",
        "current_run_id",
    )
    state = _freshness_value(target_freshness, "state")
    summary = _freshness_value(target_freshness, "summary")
    session_identity = _freshness_value(
        target_freshness,
        "sessionIdentity",
        "session_identity",
    )
    pinned_state = _freshness_value(target_freshness, "pinnedState", "pinned_state")
    pinned_summary = _freshness_value(
        target_freshness,
        "pinnedSummary",
        "pinned_summary",
    )
    pinned_session_identity = _freshness_value(
        target_freshness,
        "pinnedSessionIdentity",
        "pinned_session_identity",
    )
    target_run_changed = bool(
        _freshness_value(target_freshness, "targetRunChanged", "target_run_changed")
    )
    state_changed = (
        pinned_state is not None and state is not None and state != pinned_state
    )
    summary_changed = (
        pinned_summary is not None
        and summary is not None
        and summary != pinned_summary
    )
    session_changed = (
        pinned_session_identity is not None
        and session_identity is not None
        and session_identity != pinned_session_identity
    )
    materially_changed = (
        target_run_changed
        or (
            bool(current_run_id)
            and bool(pinned_run_id)
            and current_run_id != pinned_run_id
        )
        or state_changed
        or summary_changed
        or session_changed
    )
    return RemediationTargetFreshnessDecision(
        status="materially_changed" if materially_changed else "fresh",
        pinned_run_id=pinned_run_id or target_run_id,
        current_run_id=current_run_id,
        state=state,
        summary=summary,
        session_identity=session_identity,
        target_run_changed=target_run_changed,
    )

def _freshness_value(
    target_freshness: Mapping[str, Any] | Any,
    *names: str,
) -> Any:
    for name in names:
        if isinstance(target_freshness, Mapping) and name in target_freshness:
            return target_freshness[name]
        if hasattr(target_freshness, name):
            return getattr(target_freshness, name)
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
    "RemediationActionBudgetDecision",
    "RemediationActionLedgerDecision",
    "RemediationMutationGuardDecision",
    "RemediationMutationGuardPolicy",
    "RemediationMutationGuardResult",
    "RemediationMutationGuardService",
    "RemediationMutationLockDecision",
    "RemediationNestedDecision",
    "RemediationPermissionSet",
    "RemediationSecurityProfile",
    "RemediationTargetFreshnessDecision",
]
