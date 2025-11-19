"""Approval policy helpers for the orchestrator service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from api_service.db import models as db_models

from .repositories import OrchestratorRepository


@dataclass(slots=True)
class ApprovalPolicy:
    """Resolved approval policy for a target service."""

    gate: Optional[db_models.ApprovalGate]

    @property
    def requirement(self) -> db_models.OrchestratorApprovalRequirement:
        if self.gate is None:
            return db_models.OrchestratorApprovalRequirement.NONE
        return self.gate.requirement

    @property
    def requires_approval(self) -> bool:
        return self.requirement != db_models.OrchestratorApprovalRequirement.NONE


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def resolve_policy(
    repo: OrchestratorRepository, service_name: str
) -> ApprovalPolicy:
    """Return the approval policy for ``service_name`` if one exists."""

    gate = await repo.get_approval_gate(service_name)
    return ApprovalPolicy(gate=gate)


def validate_approval_token(
    policy: ApprovalPolicy,
    token: str | None,
    *,
    granted_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> Tuple[bool, Optional[str]]:
    """Validate ``token`` against ``policy`` returning ``(ok, reason)``."""

    if not policy.requires_approval:
        return True, None
    if not token:
        return False, "Approval token required for protected service"

    gate = policy.gate
    assert gate is not None

    now = _utcnow()
    issued = granted_at or now
    expiry_candidate = expires_at
    if expiry_candidate is None and gate.valid_for_minutes:
        expiry_candidate = issued + timedelta(minutes=gate.valid_for_minutes)

    if expiry_candidate is not None and expiry_candidate < now:
        return False, "Approval token has expired"

    return True, None


def apply_approval_snapshot(
    snapshot: dict[str, object] | None,
    *,
    policy: ApprovalPolicy,
    token: str | None,
    approver: dict[str, str] | None = None,
    granted_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict[str, object]:
    """Return a metrics snapshot annotated with approval metadata."""

    resolved = snapshot or {}
    status = "granted" if token else "awaiting"
    resolved["approval"] = {
        "required": policy.requires_approval,
        "requirement": policy.requirement.value,
        "status": status,
    }
    if approver:
        resolved["approval"]["approver"] = approver
    if granted_at:
        resolved["approval"]["grantedAt"] = granted_at.isoformat()
    if expires_at:
        resolved["approval"]["expiresAt"] = expires_at.isoformat()
    return resolved


__all__ = [
    "ApprovalPolicy",
    "apply_approval_snapshot",
    "resolve_policy",
    "validate_approval_token",
]
