from __future__ import annotations

import json

import pytest

from moonmind.workflows.temporal.step_execution_conformance import (
    FORBIDDEN_INLINE_EVIDENCE_FIXTURES,
    REQUIRED_CONFORMANCE_FAMILIES,
    REQUIRED_TRACEABILITY_IDS,
    STEP_EXECUTION_CONFORMANCE_SUITE_ID,
    api_contract_fixture,
    build_conformance_summary,
    classify_gate_verdict,
    golden_fixture_catalog,
    run_step_execution_conformance,
)
from moonmind.workflows.temporal.step_executions import (
    memory_side_effect_summary,
    memory_write_gate_decision,
    side_effect_record,
)


FORBIDDEN_OUTPUT_TOKENS = (
    "raw stdout",
    "raw stderr",
    "diff --git",
    "provider payload",
    "verification report",
    "credential value",
    "x" * 1200,
)


def test_conformance_summary_reports_all_required_families() -> None:
    summary = build_conformance_summary()

    assert summary["suite"] == STEP_EXECUTION_CONFORMANCE_SUITE_ID
    assert summary["overallResult"] == "passed"
    assert {family["family"] for family in summary["families"]} == set(
        REQUIRED_CONFORMANCE_FAMILIES
    )
    assert all(family["result"] == "passed" for family in summary["families"])


@pytest.mark.parametrize("fixture", FORBIDDEN_INLINE_EVIDENCE_FIXTURES)
def test_manifest_and_checkpoint_forbidden_inline_evidence_matrix(
    fixture: dict[str, object],
) -> None:
    decision = fixture["decision"]

    assert isinstance(decision, dict)
    assert decision["valid"] is False
    assert decision["decision"] == "invalid"
    assert decision["expected"] == "invalid"
    assert decision["failureCode"] == fixture["failureCode"]
    assert fixture["payloadClass"] in decision["message"]
    assert fixture["contractSurface"] in {"manifest", "checkpoint"}


def test_golden_fixture_catalog_is_complete_and_deterministic() -> None:
    catalog = golden_fixture_catalog()

    assert [fixture["fixtureId"] for fixture in catalog] == [
        "successful-execution",
        "failed-reattempt",
        "memory-failed-proposed",
        "memory-accepted-run-context",
        "memory-blocked-repo-write",
        "memory-superseded",
        "memory-source-identity",
        "memory-unsafe-content",
        "gate-failure",
        "recovery-with-preserved-steps",
        "degraded-checkpoint-payload",
        "degraded-gate-verdict",
        "legacy-checkpoint-only-ledger-row",
    ]
    assert {fixture["decision"]["decision"] for fixture in catalog} == {
        "valid",
        "invalid",
        "degraded",
    }
    assert {
        fixture["fixtureId"]: fixture["decision"]["decision"]
        for fixture in catalog
    }["successful-execution"] == "valid"
    assert {
        fixture["fixtureId"]: fixture["decision"]["decision"]
        for fixture in catalog
    }["legacy-checkpoint-only-ledger-row"] == "degraded"
    assert all("expected" in fixture["decision"] for fixture in catalog)


def test_manifest_and_checkpoint_writer_fixtures_use_canonical_refs() -> None:
    summary = build_conformance_summary()
    content_types = {
        item["contentType"]
        for item in summary["writerFixtures"]
        if item["contractSurface"] in {"manifest", "checkpoint"}
    }

    assert content_types == {
        "application/vnd.moonmind.step-execution+json;version=1",
        "application/vnd.moonmind.step-execution-checkpoint+json;version=1",
    }
    serialized = json.dumps(summary["writerFixtures"])
    assert "step-checkpoint" not in serialized
    assert "step-resume-checkpoint" not in serialized
    assert "summaryRef" in serialized
    assert "diffRef" in serialized


def test_degraded_inputs_return_typed_decisions_without_exceptions() -> None:
    summary = build_conformance_summary()
    decisions = {
        decision["fixtureId"]: decision
        for decision in summary["decisions"]
        if decision["category"] in {"degraded_input", "replay"}
    }

    assert decisions["old-manifest-row"]["decision"] == "invalid"
    assert decisions["old-manifest-row"]["expected"] == "invalid"
    assert decisions["old-checkpoint-row"]["decision"] == "invalid"
    assert decisions["old-checkpoint-row"]["expected"] == "invalid"
    assert decisions["blank-gate-verdict"]["decision"] == "degraded"
    assert decisions["blank-gate-verdict"]["expected"] == "degraded"
    assert decisions["unknown-gate-verdict"]["decision"] == "degraded"
    assert decisions["future-gate-verdict"]["decision"] == "degraded"
    assert decisions["legacy-checkpoint-only-ledger-row"]["decision"] == "degraded"
    assert decisions["legacy-checkpoint-only-ledger-row"]["expected"] == "degraded"


@pytest.mark.parametrize(
    "verdict",
    [
        "FULLY_IMPLEMENTED",
        "ADDITIONAL_WORK_NEEDED",
        "NO_DETERMINATION",
        "BLOCKED",
        "FAILED_UNRECOVERABLE",
    ],
)
def test_canonical_gate_verdicts_are_structured_valid_values(verdict: str) -> None:
    decision = classify_gate_verdict(f"canonical-{verdict.lower()}", verdict)

    assert decision["decision"] == "valid"
    assert decision["valid"] is True
    assert decision["failureCode"] is None


def test_conformance_messages_are_bounded_and_do_not_leak_raw_evidence() -> None:
    summary = build_conformance_summary()
    rendered = json.dumps(
        [
            decision["message"]
            for decision in summary["decisions"]
            if decision["decision"] != "valid"
        ]
    )

    assert len(rendered) < 5000
    for token in FORBIDDEN_OUTPUT_TOKENS:
        assert token not in rendered


def test_canonical_terminology_fixture_decisions_are_enforced() -> None:
    summary = build_conformance_summary()
    terminology = {
        decision["fixtureId"]: decision for decision in summary["decisions"]
    }

    assert terminology["term-step-executions"]["decision"] == "valid"
    assert terminology["term-executionOrdinal"]["decision"] == "valid"
    assert terminology["term-recover_from_failed_step"]["decision"] == "valid"
    assert terminology["term-step-attempt"]["decision"] == "invalid"
    assert terminology["term-step-attempt"]["expected"] == "invalid"


def test_expected_decision_mismatch_fails_relevant_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from moonmind.workflows.temporal import step_execution_conformance as conformance

    mutated = [
        dict(fixture) for fixture in conformance.FORBIDDEN_INLINE_EVIDENCE_FIXTURES
    ]
    first_fixture = dict(mutated[0])
    first_decision = dict(first_fixture["decision"])
    first_decision["decision"] = "valid"
    first_fixture["decision"] = first_decision
    mutated[0] = first_fixture
    monkeypatch.setattr(
        conformance, "FORBIDDEN_INLINE_EVIDENCE_FIXTURES", tuple(mutated)
    )

    summary = conformance.build_conformance_summary()
    family_by_name = {family["family"]: family for family in summary["families"]}
    failed_family = family_by_name[first_fixture["contractSurface"]]

    assert summary["overallResult"] == "failed"
    assert failed_family["result"] == "failed"
    assert first_decision["fixtureId"] in failed_family["failedFixtureIds"]


def test_traceability_matrix_covers_all_mm_820_categories() -> None:
    summary = build_conformance_summary()
    covered = {
        trace_id
        for decision in summary["decisions"]
        for trace_id in decision["traceability"]
    }

    assert set(REQUIRED_TRACEABILITY_IDS).issubset(covered)


def test_api_contract_fixture_exposes_refs_and_canonical_terms_only() -> None:
    fixture = api_contract_fixture()

    assert fixture["route"] == "/api/executions/{execution_id}/step-executions"
    assert fixture["projection"]["executionOrdinal"] == 2
    assert fixture["projection"]["recoveryAction"] == "recover_from_failed_step"
    assert "providerPayload" not in fixture["projection"]
    assert "verificationReport" not in fixture["projection"]
    assert fixture["projection"]["artifactRefs"]["manifestRef"].startswith(
        "artifact://"
    )


def test_run_step_execution_conformance_returns_one_aggregate_result() -> None:
    result = run_step_execution_conformance()

    assert result["overallResult"] == "passed"
    assert result["failedFixtureIds"] == []
    assert set(result["familyResults"]) == set(REQUIRED_CONFORMANCE_FAMILIES)


def test_memory_side_effects_gate_failed_attempts_and_repo_writes() -> None:
    failed = memory_write_gate_decision(
        target="memory://run",
        terminal_disposition="retryable",
        publication_gate_passed=False,
        policy_decision="blocked",
    )
    run_local = memory_write_gate_decision(
        target="memory://run",
        terminal_disposition="retryable",
        publication_gate_passed=False,
        policy_decision="accept_for_run_context",
    )
    repo_blocked = memory_write_gate_decision(
        target="repo://AGENTS.md",
        terminal_disposition="retryable",
        publication_gate_passed=True,
        policy_decision="approve_repo_application",
    )

    assert failed["state"] == "proposed"
    assert failed["allowed"] is False
    assert run_local["state"] == "accepted_for_run_context"
    assert run_local["allowed"] is True
    assert repo_blocked["state"] == "proposed"
    assert repo_blocked["allowed"] is False
    assert repo_blocked["reason"] == "terminal_disposition_not_accepted"


def test_memory_side_effect_summary_preserves_policy_refs_and_privileged_candidate() -> None:
    source = {
        "workflowId": "workflow-1",
        "runId": "run-1",
        "logicalStepId": "implement-story",
        "executionOrdinal": 2,
    }
    summary = memory_side_effect_summary(
        state="applied_to_repo",
        target="repo://AGENTS.md",
        reason="accepted_disposition_and_publication_gate_passed",
        proposal_ref="artifact://memory/proposal-1",
        decision_ref="artifact://memory/decision-1",
        application_result_ref="artifact://memory/application-1",
        source=source,
        privileged_action={
            "actor": "workflow://workflow-1",
            "action": "memory.apply_repo",
            "target": "repo://AGENTS.md",
            "reason": "approved_repo_application",
            "decision": "approve_repo_application",
            "evidenceRefs": ["artifact://memory/decision-1"],
        },
    )
    record = side_effect_record(
        effect_class="memory_update",
        operation="memory.apply_repo",
        target="repo://AGENTS.md",
        workflow_state_accepted=True,
        memory_effect=summary,
    )

    assert record["memory"]["state"] == "applied_to_repo"
    assert record["memory"]["privilegedAction"]["action"] == "memory.apply_repo"
    assert record["memory"]["source"] == source
