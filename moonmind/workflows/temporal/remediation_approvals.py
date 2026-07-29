"""Deterministic, replay-safe remediation approval-request contracts.

Issue #3512, Area 2. Approval for an approval-gated remediation action is a
first-class, durable, actor-attributed decision — not a chat message and never
inferred from agent text. A request pins the exact target/action it authorizes,
an expected-state snapshot, the policy version it was evaluated under, and an
expiration. A decision is idempotent under Temporal replay/retry and is rejected
when the target state, checkpoint, host/session identity, credential generation,
or policy version changed after the request was persisted.

The models here carry only compact, non-sensitive identifiers and refs so they
are safe to persist as ``remediation.approval_request`` artifacts and to carry
across Continue-As-New.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Contract(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "stale"]
ApprovalRiskTier = Literal["low", "medium", "high"]


def _coerce_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if not text:
        raise ValueError("timestamp is required")
    normalized = text.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class RemediationApprovalExpectedState(_Contract):
    """The pinned target state an approval decision remains bound to."""

    target_run_id: str = Field(alias="targetRunId")
    target_state: str | None = Field(default=None, alias="targetState")
    checkpoint_ref: str | None = Field(default=None, alias="checkpointRef")
    host_session_identity: str | None = Field(
        default=None, alias="hostSessionIdentity"
    )
    credential_generation: str | None = Field(
        default=None, alias="credentialGeneration"
    )

    @field_validator("target_run_id")
    @classmethod
    def _run_id_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("expectedState.targetRunId is required")
        return value


class RemediationApprovalRequest(_Contract):
    """A durable, replay-safe approval request for one remediation action."""

    schema_version: Literal["v1"] = Field(default="v1", alias="schemaVersion")
    kind: Literal["remediation.approval_request"] = "remediation.approval_request"
    request_id: str = Field(alias="requestId")
    remediation_workflow_id: str = Field(alias="remediationWorkflowId")
    remediation_run_id: str = Field(alias="remediationRunId")
    target_workflow_id: str = Field(alias="targetWorkflowId")
    action_kind: str = Field(alias="actionKind")
    idempotency_key: str = Field(alias="idempotencyKey")
    risk_tier: ApprovalRiskTier = Field(alias="riskTier")
    # High-risk actions (for example force termination or equivalent) require an
    # explicit stronger approval grant, not an ordinary approval.
    requires_strong_approval: bool = Field(
        default=False, alias="requiresStrongApproval"
    )
    expected_state: RemediationApprovalExpectedState = Field(alias="expectedState")
    policy_version: str = Field(alias="policyVersion")
    created_at: datetime = Field(alias="createdAt")
    expires_at: datetime = Field(alias="expiresAt")
    status: ApprovalStatus = "pending"
    decision_actor: str | None = Field(default=None, alias="decisionActor")
    decision_rationale: str | None = Field(default=None, alias="decisionRationale")
    decided_at: datetime | None = Field(default=None, alias="decidedAt")
    stale_reason: str | None = Field(default=None, alias="staleReason")

    @field_validator(
        "request_id",
        "remediation_workflow_id",
        "remediation_run_id",
        "target_workflow_id",
        "action_kind",
        "idempotency_key",
        "policy_version",
    )
    @classmethod
    def _required_identifier(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("approval identifier fields are required")
        return value

    def project(self) -> dict[str, object]:
        """Return the compact API/UI projection of this approval request."""

        return self.model_dump(by_alias=True, mode="json", exclude_none=True)


class RemediationApprovalDecisionError(ValueError):
    """Raised when an approval decision request is structurally invalid."""


def _staleness_reason(
    request: RemediationApprovalRequest,
    *,
    observed_state: RemediationApprovalExpectedState,
    observed_policy_version: str,
) -> str | None:
    expected = request.expected_state
    if observed_state.target_run_id != expected.target_run_id:
        return "target_run_changed"
    if (
        expected.target_state is not None
        and observed_state.target_state != expected.target_state
    ):
        return "target_state_changed"
    if (
        expected.checkpoint_ref is not None
        and observed_state.checkpoint_ref != expected.checkpoint_ref
    ):
        return "checkpoint_changed"
    if (
        expected.host_session_identity is not None
        and observed_state.host_session_identity != expected.host_session_identity
    ):
        return "host_session_identity_changed"
    if (
        expected.credential_generation is not None
        and observed_state.credential_generation != expected.credential_generation
    ):
        return "credential_generation_changed"
    if observed_policy_version.strip() != request.policy_version:
        return "policy_version_changed"
    return None


def decide_remediation_approval(
    request: RemediationApprovalRequest,
    *,
    decision: Literal["approved", "rejected"],
    actor: str,
    now: datetime | str,
    observed_state: RemediationApprovalExpectedState,
    observed_policy_version: str,
    strong_approval_granted: bool = False,
    rationale: str | None = None,
) -> RemediationApprovalRequest:
    """Return the deterministically decided approval request.

    Replay/retry safety: a request that is already terminal (approved, rejected,
    expired, or stale) is returned unchanged, so Temporal replay or a duplicate
    operator submission can never duplicate or reinterpret a decision. Expiration
    is evaluated before the decision, and a request whose bound state or policy
    version drifted is rejected as ``stale`` rather than silently applied.
    """

    if decision not in ("approved", "rejected"):
        raise RemediationApprovalDecisionError(
            "decision must be 'approved' or 'rejected'"
        )
    actor = actor.strip()
    if not actor:
        raise RemediationApprovalDecisionError("decision actor is required")

    # Idempotent replay: never reinterpret a decision that already landed.
    if request.status != "pending":
        return request

    now_dt = _coerce_datetime(now)
    if now_dt >= request.expires_at:
        return request.model_copy(
            update={"status": "expired", "decided_at": now_dt}
        )

    stale_reason = _staleness_reason(
        request,
        observed_state=observed_state,
        observed_policy_version=observed_policy_version,
    )
    if stale_reason is not None:
        return request.model_copy(
            update={
                "status": "stale",
                "stale_reason": stale_reason,
                "decided_at": now_dt,
            }
        )

    if (
        decision == "approved"
        and request.requires_strong_approval
        and not strong_approval_granted
    ):
        raise RemediationApprovalDecisionError(
            "high-risk remediation action requires an explicit stronger approval "
            "grant"
        )

    return request.model_copy(
        update={
            "status": decision,
            "decision_actor": actor,
            "decision_rationale": (rationale or "").strip() or None,
            "decided_at": now_dt,
        }
    )


__all__ = [
    "ApprovalRiskTier",
    "ApprovalStatus",
    "RemediationApprovalDecisionError",
    "RemediationApprovalExpectedState",
    "RemediationApprovalRequest",
    "decide_remediation_approval",
]
