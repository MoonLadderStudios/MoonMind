"""Deterministic approval-request coverage for issue #3512, Area 2."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from moonmind.workflows.temporal.remediation_approvals import (
    RemediationApprovalDecisionError,
    RemediationApprovalExpectedState,
    RemediationApprovalRequest,
    decide_remediation_approval,
)

_CREATED = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
_NOW = _CREATED + timedelta(minutes=5)
_EXPIRES = _CREATED + timedelta(minutes=30)


def _expected(**overrides) -> RemediationApprovalExpectedState:
    base = {
        "targetRunId": "run-t",
        "targetState": "failed",
        "checkpointRef": "artifact://ckpt",
        "hostSessionIdentity": "host:sess:1",
        "credentialGeneration": "gen-1",
    }
    base.update(overrides)
    return RemediationApprovalExpectedState.model_validate(base)


def _request(**overrides) -> RemediationApprovalRequest:
    base = {
        "requestId": "ract-1",
        "remediationWorkflowId": "mm:remediate",
        "remediationRunId": "run-r",
        "targetWorkflowId": "mm:target",
        "actionKind": "execution.resume",
        "idempotencyKey": "idem-1",
        "riskTier": "medium",
        "expectedState": _expected().model_dump(by_alias=True),
        "policyVersion": "policy-v1",
        "createdAt": _CREATED,
        "expiresAt": _EXPIRES,
    }
    base.update(overrides)
    return RemediationApprovalRequest.model_validate(base)


def test_request_projects_bounded_fields():
    payload = _request().project()
    assert payload["kind"] == "remediation.approval_request"
    assert payload["status"] == "pending"
    assert payload["expectedState"]["targetRunId"] == "run-t"
    assert payload["policyVersion"] == "policy-v1"


def test_approval_records_actor_and_rationale():
    decided = decide_remediation_approval(
        _request(),
        decision="approved",
        actor="operator@example.com",
        now=_NOW,
        observed_state=_expected(),
        observed_policy_version="policy-v1",
        rationale="looks safe",
    )
    assert decided.status == "approved"
    assert decided.decision_actor == "operator@example.com"
    assert decided.decision_rationale == "looks safe"
    assert decided.decided_at == _NOW


def test_replay_is_idempotent_and_never_reinterprets():
    approved = decide_remediation_approval(
        _request(),
        decision="approved",
        actor="a",
        now=_NOW,
        observed_state=_expected(),
        observed_policy_version="policy-v1",
    )
    # A replay / duplicate submission with a *different* decision must not change
    # the landed decision.
    replayed = decide_remediation_approval(
        approved,
        decision="rejected",
        actor="b",
        now=_NOW + timedelta(minutes=1),
        observed_state=_expected(),
        observed_policy_version="policy-v1",
    )
    assert replayed.status == "approved"
    assert replayed.decision_actor == "a"


def test_expired_request_cannot_be_decided():
    decided = decide_remediation_approval(
        _request(),
        decision="approved",
        actor="a",
        now=_EXPIRES + timedelta(seconds=1),
        observed_state=_expected(),
        observed_policy_version="policy-v1",
    )
    assert decided.status == "expired"


@pytest.mark.parametrize(
    "observed_overrides,observed_policy,expected_reason",
    [
        ({"targetRunId": "run-t2"}, "policy-v1", "target_run_changed"),
        ({"targetState": "completed"}, "policy-v1", "target_state_changed"),
        ({"checkpointRef": "artifact://other"}, "policy-v1", "checkpoint_changed"),
        (
            {"hostSessionIdentity": "host:sess:2"},
            "policy-v1",
            "host_session_identity_changed",
        ),
        (
            {"credentialGeneration": "gen-2"},
            "policy-v1",
            "credential_generation_changed",
        ),
        ({}, "policy-v2", "policy_version_changed"),
    ],
)
def test_stale_binding_is_rejected(
    observed_overrides, observed_policy, expected_reason
):
    decided = decide_remediation_approval(
        _request(),
        decision="approved",
        actor="a",
        now=_NOW,
        observed_state=_expected(**observed_overrides),
        observed_policy_version=observed_policy,
    )
    assert decided.status == "stale"
    assert decided.stale_reason == expected_reason


def test_high_risk_requires_stronger_approval_grant():
    request = _request(riskTier="high", requiresStrongApproval=True)
    with pytest.raises(RemediationApprovalDecisionError, match="stronger approval"):
        decide_remediation_approval(
            request,
            decision="approved",
            actor="a",
            now=_NOW,
            observed_state=_expected(),
            observed_policy_version="policy-v1",
        )
    granted = decide_remediation_approval(
        request,
        decision="approved",
        actor="a",
        now=_NOW,
        observed_state=_expected(),
        observed_policy_version="policy-v1",
        strong_approval_granted=True,
    )
    assert granted.status == "approved"


def test_rejection_records_decision():
    decided = decide_remediation_approval(
        _request(),
        decision="rejected",
        actor="a",
        now=_NOW,
        observed_state=_expected(),
        observed_policy_version="policy-v1",
        rationale="unsafe",
    )
    assert decided.status == "rejected"
    assert decided.decision_rationale == "unsafe"


def test_iso_string_time_inputs_are_accepted():
    decided = decide_remediation_approval(
        _request(),
        decision="approved",
        actor="a",
        now="2026-07-29T12:05:00Z",
        observed_state=_expected(),
        observed_policy_version="policy-v1",
    )
    assert decided.status == "approved"
