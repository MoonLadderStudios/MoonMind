import json

import pytest

from moonmind.workflows.temporal.activity_runtime import TemporalIntegrationActivities


pytestmark = pytest.mark.asyncio


def _source(logical_step_id: str = "implement", execution_ordinal: int = 1) -> dict[str, object]:
    return {
        "workflowId": "wf-memory",
        "runId": "run-memory",
        "logicalStepId": logical_step_id,
        "executionOrdinal": execution_ordinal,
    }


def _publication_gate(passed: bool) -> dict[str, object]:
    return {"passed": passed, "evidenceRef": "artifact://gate/publication"}


@pytest.fixture
def activities() -> TemporalIntegrationActivities:
    return TemporalIntegrationActivities()


async def test_memory_evaluate_boundary_blocks_unknown_and_rejects_proposals(
    activities: TemporalIntegrationActivities,
) -> None:
    unknown = await activities.memory_evaluate_proposals(
        proposal_refs=["artifact://memory/proposal-unknown"],
        source=_source(),
        terminal_disposition="accepted",
        publication_gate=_publication_gate(False),
        requested_target="memory://run",
        policy_decision="invented_state",
        evidence_refs=["artifact://memory/proposal-unknown"],
    )
    rejected = await activities.memory_evaluate_proposals(
        proposal_refs=["artifact://memory/proposal-rejected"],
        source=_source(),
        terminal_disposition="accepted",
        publication_gate=_publication_gate(False),
        requested_target="memory://run",
        policy_decision="reject",
        reason="operator_rejected_candidate",
        evidence_refs=["artifact://memory/proposal-rejected"],
    )

    assert unknown["decisions"][0]["decision"] == "blocked"
    assert unknown["decisions"][0]["reason"] == "unknown_policy_decision"
    assert rejected["decisionRefs"] == ["artifact://memory/decision-1"]
    assert rejected["decisions"][0]["decision"] == "reject"
    assert rejected["decisions"][0]["proposalRef"] == "artifact://memory/proposal-rejected"


async def test_memory_run_context_boundary_applies_with_source_provenance(
    activities: TemporalIntegrationActivities,
) -> None:
    source = _source(execution_ordinal=2)
    decision = await activities.memory_evaluate_proposals(
        proposal_refs=["artifact://memory/proposal-run"],
        source=source,
        terminal_disposition="retryable",
        publication_gate=_publication_gate(False),
        requested_target="memory://run",
        policy_decision="accept_for_run_context",
        reason="policy_approved_for_later_attempts",
        evidence_refs=["artifact://memory/proposal-run"],
    )
    application = await activities.memory_apply_policy(
        proposal_ref="artifact://memory/proposal-run",
        decision_ref=decision["decisionRefs"][0],
        source=source,
        target="memory://run",
        decision=decision["decisions"][0]["decision"],
        gate_status={
            "terminalDisposition": "retryable",
            "publicationGate": False,
            "policyGate": True,
        },
    )

    assert decision["decisions"][0]["decision"] == "accept_for_run_context"
    assert application == {
        "applicationResultRef": "artifact://memory/application-1",
        "outcome": "applied",
        "target": "memory://run",
        "resultRef": "artifact://memory/run-context-1",
        "failureReason": None,
    }


@pytest.mark.parametrize("terminal_disposition", ["retryable", "blocked", "discarded", None])
async def test_memory_repo_boundary_blocks_failed_or_abandoned_attempts(
    activities: TemporalIntegrationActivities,
    terminal_disposition: str | None,
) -> None:
    decision = await activities.memory_evaluate_proposals(
        proposal_refs=["artifact://memory/proposal-repo"],
        source=_source(logical_step_id="publish"),
        terminal_disposition=terminal_disposition,
        publication_gate=_publication_gate(True),
        requested_target="repo://AGENTS.md",
        policy_decision="approve_repo_application",
        reason="candidate_repo_memory",
        evidence_refs=["artifact://memory/proposal-repo"],
    )
    application = await activities.memory_apply_policy(
        proposal_ref="artifact://memory/proposal-repo",
        decision_ref=decision["decisionRefs"][0],
        source=_source(logical_step_id="publish"),
        target="repo://AGENTS.md",
        decision=decision["decisions"][0]["decision"],
        gate_status={
            "terminalDisposition": terminal_disposition,
            "publicationGate": True,
            "policyGate": decision["decisions"][0]["decision"] != "blocked",
        },
    )

    assert decision["decisions"][0]["decision"] == "blocked"
    assert decision["decisions"][0]["reason"] == "terminal_disposition_not_accepted"
    assert application["outcome"] == "blocked"
    assert application["failureReason"] == "policy_decision_not_approving"


async def test_memory_repo_boundary_applies_only_with_accepted_gates(
    activities: TemporalIntegrationActivities,
) -> None:
    source = _source(logical_step_id="publish")
    decision = await activities.memory_evaluate_proposals(
        proposal_refs=["artifact://memory/proposal-repo"],
        source=source,
        terminal_disposition="accepted",
        publication_gate=_publication_gate(True),
        requested_target="repo://AGENTS.md",
        policy_decision="approve_repo_application",
        reason="accepted_disposition_and_publication_gate_passed",
        evidence_refs=["artifact://memory/proposal-repo"],
    )
    application = await activities.memory_apply_policy(
        proposal_ref="artifact://memory/proposal-repo",
        decision_ref=decision["decisionRefs"][0],
        source=source,
        target="repo://AGENTS.md",
        decision=decision["decisions"][0]["decision"],
        result_ref="artifact://memory/repo-application-1",
        gate_status={
            "terminalDisposition": "accepted",
            "publicationGate": True,
            "policyGate": True,
        },
    )

    assert decision["decisions"][0]["decision"] == "approve_repo_application"
    assert application == {
        "applicationResultRef": "artifact://memory/application-1",
        "outcome": "applied",
        "target": "repo://AGENTS.md",
        "resultRef": "artifact://memory/repo-application-1",
        "failureReason": None,
    }


async def test_memory_superseded_boundary_preserves_refs(
    activities: TemporalIntegrationActivities,
) -> None:
    result = await activities.memory_evaluate_proposals(
        proposal_refs=[
            "artifact://memory/proposal-old",
            "artifact://memory/proposal-new",
        ],
        source=_source(execution_ordinal=3),
        terminal_disposition="accepted",
        publication_gate=_publication_gate(False),
        requested_target="memory://run",
        policy_decision="supersede",
        reason="replacement_available",
        evidence_refs=["artifact://memory/proposal-new"],
    )

    assert result["decisionRefs"] == [
        "artifact://memory/decision-1",
        "artifact://memory/decision-2",
    ]
    assert [decision["decision"] for decision in result["decisions"]] == [
        "supersede",
        "supersede",
    ]
    assert [decision["proposalRef"] for decision in result["decisions"]] == [
        "artifact://memory/proposal-old",
        "artifact://memory/proposal-new",
    ]


async def test_memory_activity_outputs_are_compact_ref_payloads(
    activities: TemporalIntegrationActivities,
) -> None:
    result = await activities.memory_evaluate_proposals(
        proposal_refs=["artifact://memory/proposal-compact"],
        source=_source(),
        terminal_disposition="accepted",
        publication_gate=_publication_gate(False),
        requested_target="memory://run",
        policy_decision="accept_for_run_context",
        reason="policy_approved_for_later_attempts",
        evidence_refs=["artifact://memory/proposal-compact"],
    )

    rendered = json.dumps(result)

    assert "artifact://memory/proposal-compact" in rendered
    assert "raw stdout" not in rendered
    assert "raw stderr" not in rendered
    assert "diff --git" not in rendered
    assert "provider payload" not in rendered
    assert "token=" not in rendered
    assert len(rendered) < 2000
