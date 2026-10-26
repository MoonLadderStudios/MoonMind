from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from moonmind.schemas.step_execution_models import (
    MemoryApplicationResultManifest,
    MemoryPolicyDecisionManifest,
    StepExecutionIdentityModel,
)


def evaluate_memory_proposals(
    *,
    proposal_refs: list[str],
    source: dict[str, Any],
    terminal_disposition: str | None,
    publication_gate: dict[str, Any] | None,
    requested_target: str,
    policy_decision: str | None = None,
    reason: str | None = None,
    evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate memory proposal refs and return compact policy decisions."""

    source_identity = StepExecutionIdentityModel.model_validate(source)
    normalized_refs = [str(ref).strip() for ref in proposal_refs if str(ref).strip()]
    gate = dict(publication_gate or {})
    publication_passed = gate.get("passed") is True
    decision = str(policy_decision or "").strip()
    repo_target = str(requested_target or "").strip().startswith("repo://")
    if decision not in {
        "reject",
        "accept_for_run_context",
        "approve_repo_application",
        "supersede",
        "blocked",
    }:
        decision = "blocked"
        resolved_reason = "unknown_policy_decision"
    elif not repo_target and decision == "approve_repo_application":
        decision = "blocked"
        resolved_reason = "memory_target_requires_run_context_decision"
    elif decision == "approve_repo_application" and (
        terminal_disposition != "accepted" or not publication_passed
    ):
        decision = "blocked"
        resolved_reason = (
            "terminal_disposition_not_accepted"
            if terminal_disposition != "accepted"
            else "publication_gate_not_passed"
        )
    elif repo_target and decision == "accept_for_run_context":
        decision = "blocked"
        resolved_reason = "repo_target_requires_repo_application_decision"
    else:
        resolved_reason = str(reason or "").strip() or "policy_decision_recorded"

    decisions: list[dict[str, Any]] = []
    decision_refs: list[str] = []
    for index, proposal_ref in enumerate(normalized_refs, start=1):
        decision_ref = f"artifact://memory/decision-{index}"
        manifest = MemoryPolicyDecisionManifest(
            decisionId=f"decision-{index}",
            proposalRef=proposal_ref,
            source=source_identity,
            target=requested_target,
            reason=resolved_reason,
            decision=decision,
            decisionRef=decision_ref,
            evidenceRefs=evidence_refs or [proposal_ref],
            gateStatus={
                "terminalDisposition": terminal_disposition,
                "publicationGate": publication_passed,
                "publicationGateEvidenceRef": gate.get("evidenceRef"),
                "policyGate": decision != "blocked",
            },
            createdAt=datetime.now(UTC),
        )
        decision_refs.append(decision_ref)
        decisions.append(
            {
                "proposalRef": manifest.proposal_ref,
                "decision": manifest.decision,
                "target": manifest.target,
                "reason": manifest.reason,
                "evidenceRefs": manifest.evidence_refs,
                "decisionRef": manifest.decision_ref,
            }
        )
    return {"decisionRefs": decision_refs, "decisions": decisions}


def apply_memory_policy(
    *,
    proposal_ref: str,
    decision_ref: str,
    source: dict[str, Any],
    target: str,
    decision: str,
    result_ref: str | None = None,
    gate_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply an approved memory decision or return a blocked result."""

    source_identity = StepExecutionIdentityModel.model_validate(source)
    proposal = str(proposal_ref or "").strip()
    decision_artifact_ref = str(decision_ref or "").strip()
    repo_target = str(target or "").strip().startswith("repo://")
    gate = dict(gate_status or {})
    gates_passed = (
        gate.get("terminalDisposition") == "accepted"
        and gate.get("publicationGate") is True
        and gate.get("policyGate", True) is True
    )
    if not decision_artifact_ref:
        outcome = "blocked"
        failure_reason = "missing_decision_ref"
    elif decision not in {"accept_for_run_context", "approve_repo_application"}:
        outcome = "blocked"
        failure_reason = "policy_decision_not_approving"
    elif decision == "approve_repo_application" and repo_target and not gates_passed:
        outcome = "blocked"
        failure_reason = "applied_repo_memory_result_requires_accepted_gates"
    else:
        outcome = "applied"
        failure_reason = None
    application_ref = "artifact://memory/application-1"
    resolved_result_ref = result_ref or (
        "artifact://memory/run-context-1"
        if outcome == "applied" and str(target).startswith("memory://")
        else "artifact://memory/repo-application-1"
        if outcome == "applied"
        else None
    )
    manifest = MemoryApplicationResultManifest(
        applicationId="application-1",
        proposalRef=proposal,
        decisionRef=decision_artifact_ref or application_ref,
        source=source_identity,
        target=target,
        outcome=outcome,
        resultRef=resolved_result_ref,
        failureReason=failure_reason,
        gateStatus=gate,
        createdAt=datetime.now(UTC),
    )
    return {
        "applicationResultRef": application_ref,
        "outcome": manifest.outcome,
        "target": manifest.target,
        "resultRef": manifest.result_ref,
        "failureReason": manifest.failure_reason,
    }
