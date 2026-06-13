from __future__ import annotations

import pytest

from moonmind.workflows.temporal.step_executions import (
    handoff_gate_side_effect_record,
    side_effect_record,
)


def _allowed_decision() -> dict[str, object]:
    return {
        "allowed": True,
        "decision": "allow",
        "handoffClass": "jira_comment",
        "actor": {"type": "workflow", "id": "MoonMindRunWorkflow"},
        "action": "jira.add_comment",
        "target": {"type": "jira_issue", "id": "MM-123"},
        "policyDecision": {
            "decision": "allow",
            "reason": "accepted_step_execution_and_passing_gate",
        },
        "idempotencyKey": "wf-1:run-1:verify:execution:1:jira.add_comment:MM-123",
        "gateSource": {
            "gateType": "moonspec_verify",
            "verdict": "FULLY_IMPLEMENTED",
            "passed": True,
            "evidenceRef": "artifact://verify/report",
        },
        "dispositionSource": {
            "stepExecutionId": "wf-1:run-1:verify:execution:1",
            "terminalDisposition": "accepted",
            "manifestRef": "artifact://step-executions/verify-1-terminal",
        },
        "evidenceRefs": ["artifact://verify/report"],
    }


def test_handoff_record_includes_governance_ready_fields() -> None:
    record = handoff_gate_side_effect_record(
        _allowed_decision(), effect_class="external_idempotent"
    )

    assert record["actor"] == {"type": "workflow", "id": "MoonMindRunWorkflow"}
    assert record["action"] == "jira.add_comment"
    assert record["target"] == {"type": "jira_issue", "id": "MM-123"}
    assert record["policyDecision"]["decision"] == "allow"
    assert record["evidenceRefs"] == ["artifact://verify/report"]
    assert record["gateSource"]["passed"] is True
    assert record["dispositionSource"]["terminalDisposition"] == "accepted"
    assert record["idempotencyKey"]


@pytest.mark.parametrize(
    "effect_class",
    [
        "workspace_mutation",
        "artifact_write",
        "external_idempotent",
        "external_non_idempotent",
        "publication",
        "provider_account",
        "memory_update",
        "retrieval_index_update",
    ],
)
def test_supported_side_effect_classifications(effect_class: str) -> None:
    record = side_effect_record(
        effect_class=effect_class,
        operation="provider_profile.lease.acquire"
        if effect_class == "provider_account"
        else f"{effect_class}.record",
        target="artifact://ref-only",
        idempotency_key="stable-key"
        if effect_class in {"external_idempotent", "provider_account", "publication"}
        else None,
        workflow_state_accepted=True,
    )

    assert record["class"] == effect_class


@pytest.mark.parametrize(
    "raw_value",
    [
        "password=super-secret",
        "token=super-secret",
        "ghp_abcdefghijklmnopqrstuvwxyz",
        "github_pat_abcdefghijklmnopqrstuvwxyz",
        "-----BEGIN PRIVATE KEY-----",
    ],
)
def test_compact_records_redact_secret_like_values(raw_value: str) -> None:
    record = side_effect_record(
        effect_class="artifact_write",
        operation="artifact.write",
        target=raw_value,
        reason=f"diagnostics included {raw_value}",
    )

    dumped = str(record)
    assert "super-secret" not in dumped
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in dumped
    assert "github_pat_abcdefghijklmnopqrstuvwxyz" not in dumped
    assert "PRIVATE KEY" not in dumped
    assert "[REDACTED]" in dumped


def test_provider_lease_and_docker_diagnostic_records_are_ref_only() -> None:
    lease = side_effect_record(
        effect_class="provider_account",
        operation="provider_profile.lease.acquire",
        target="provider-profile:codex",
        idempotency_key="lease:run-1",
        workflow_state_accepted=True,
        evidence_refs=["artifact://provider/lease"],
        actor={"type": "activity", "id": "provider_profile"},
        policy_decision={"decision": "allow", "reason": "lease_available"},
    )
    docker = side_effect_record(
        effect_class="provider_account",
        operation="docker_sidecar.credential_diagnostic",
        target="ghcr.io/moonmind/runtime",
        idempotency_key="docker-diag:run-1",
        workflow_state_accepted=True,
        evidence_refs=["artifact://docker/diagnostic"],
        diagnostics={"credentialMaterialized": True, "stdout": "token=secret"},
    )

    assert lease["evidenceRefs"] == ["artifact://provider/lease"]
    assert docker["evidenceRefs"] == ["artifact://docker/diagnostic"]
    dumped = str({"lease": lease, "docker": docker})
    assert "token=secret" not in dumped
    assert "stdout" not in dumped
