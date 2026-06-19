from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from moonmind.workflows.temporal.step_execution_conformance import (
    CLEANUP_GATE_REQUIRED_CONDITIONS,
    REQUIRED_CONFORMANCE_FAMILIES,
    api_contract_fixture,
    api_degraded_projection_decisions,
    build_conformance_summary,
    cleanup_gate_decision,
    classify_gate_verdict,
    golden_fixture_catalog,
    replay_degraded_fixture_decisions,
)


pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_single_conformance_command_runs_all_fixture_families() -> None:
    result = subprocess.run(
        [str(REPO_ROOT / "tools/test_step_execution_conformance.sh")],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    summary = json.loads(result.stdout.strip().splitlines()[-1])

    assert summary["overallResult"] == "passed"
    assert {family["family"] for family in summary["families"]} == set(
        REQUIRED_CONFORMANCE_FAMILIES
    )


def test_command_reports_complete_golden_fixture_catalog() -> None:
    fixture_ids = {fixture["fixtureId"] for fixture in golden_fixture_catalog()}

    assert fixture_ids == {
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
        "bounded-story-loop-contract",
        "bounded-story-loop-ref-only-evidence",
    }


def test_bounded_story_loop_traceability_survives_integration_conformance() -> None:
    summary = build_conformance_summary()
    covered = {
        trace_id
        for decision in summary["decisions"]
        for trace_id in decision["traceability"]
    }

    assert {"FR-018", "FR-019", "SC-006", "DESIGN-REQ-009"}.issubset(covered)


def test_gate_and_legacy_ledger_replay_inputs_are_typed_degraded() -> None:
    decisions = {
        decision["fixtureId"]: decision
        for decision in replay_degraded_fixture_decisions()
    }

    assert decisions["blank-gate-verdict"]["decision"] == "degraded"
    assert decisions["unknown-gate-verdict"]["decision"] == "degraded"
    assert decisions["future-gate-verdict"]["decision"] == "degraded"
    assert decisions["legacy-checkpoint-only-ledger-row"]["decision"] == "degraded"
    assert all(decision["failureCode"] for decision in decisions.values())


def test_full_canonical_gate_verdict_set_is_accepted() -> None:
    decisions = {
        verdict: classify_gate_verdict(f"canonical-{verdict.lower()}", verdict)
        for verdict in (
            "FULLY_IMPLEMENTED",
            "ADDITIONAL_WORK_NEEDED",
            "NO_DETERMINATION",
            "BLOCKED",
            "FAILED_UNRECOVERABLE",
        )
    }

    assert {decision["decision"] for decision in decisions.values()} == {"valid"}
    assert all(decision["failureCode"] is None for decision in decisions.values())


def test_replay_style_old_rows_return_typed_decisions() -> None:
    decisions = {
        decision["fixtureId"]: decision
        for decision in replay_degraded_fixture_decisions()
    }

    assert decisions["old-manifest-row"]["decision"] == "invalid"
    assert decisions["old-checkpoint-row"]["decision"] == "invalid"
    assert decisions["old-gate-verdict"]["decision"] == "degraded"
    assert decisions["legacy-checkpoint-only-ledger-row"]["decision"] == "degraded"


def test_command_exits_nonzero_when_family_fails() -> None:
    result = subprocess.run(
        [
            str(REPO_ROOT / "tools/test_step_execution_conformance.sh"),
            "--simulate-family-failure",
            "gate",
        ],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    summary = json.loads(result.stdout.strip().splitlines()[-1])

    assert result.returncode == 1
    assert summary["overallResult"] == "failed"
    assert summary["families"][0]["failedFixtureIds"] == ["simulated-gate-failure"]
    assert len(json.dumps(summary)) < 3000


def test_api_fixtures_expose_artifact_refs_not_inline_evidence() -> None:
    fixture = api_contract_fixture()
    rendered = json.dumps(fixture)

    assert fixture["route"].endswith("/step-executions")
    assert "manifestRef" in rendered
    assert "checkpointRef" in rendered
    assert fixture["projection"]["stepEvidence"]["checkpointRefsByBoundary"][
        "before_execution"
    ]["status"] == "available"
    assert fixture["projection"]["recoveryEligibility"]["defaultAction"] == (
        "resume_from_checkpoint"
    )
    for forbidden in ("transcript", "diff --git", "providerPayload", "credentials"):
        assert forbidden not in rendered


def test_api_projection_degraded_values_are_conformance_evidence() -> None:
    decisions = {
        decision["fixtureId"]: decision
        for decision in api_degraded_projection_decisions()
    }

    assert {
        "api-blank-step-execution-value",
        "api-unknown-step-execution-value",
        "api-future-step-execution-value",
        "api-malformed-step-execution-value",
        "api-unsupported-step-execution-ref",
    }.issubset(decisions)
    assert all(decision["decision"] == "invalid" for decision in decisions.values())
    assert all(decision["failureCode"] for decision in decisions.values())
    assert all(
        {"FR-004", "SC-003", "SCN-003", "DESIGN-REQ-021"}.issubset(
            set(decision["traceability"])
        )
        for decision in decisions.values()
    )


def test_cleanup_gate_keeps_plan_until_blockers_close_and_requires_deletion() -> None:
    conditions = {condition: True for condition in CLEANUP_GATE_REQUIRED_CONDITIONS}
    blocked_conditions = {
        **conditions,
        "docs_match_implemented_behavior": False,
    }

    blocked = cleanup_gate_decision(
        conditions=blocked_conditions,
        temp_gap_plan_exists=True,
        expected="invalid",
    )
    stale = cleanup_gate_decision(
        conditions=conditions,
        temp_gap_plan_exists=True,
    )
    closed = cleanup_gate_decision(
        conditions=conditions,
        temp_gap_plan_exists=False,
    )

    assert blocked["decision"] == "invalid"
    assert blocked["expected"] == "invalid"
    assert stale["decision"] == "invalid"
    assert stale["failureCode"] == "obsolete_gap_plan_present"
    assert closed["decision"] == "valid"
    assert {"FR-007", "FR-008", "SC-004", "DESIGN-REQ-024"}.issubset(
        set(closed["traceability"])
    )


def test_writer_fixtures_reject_superseded_content_type_spellings() -> None:
    summary = build_conformance_summary()
    rendered = json.dumps(summary["writerFixtures"])

    assert "step-checkpoint" not in rendered
    assert "step-resume-checkpoint" not in rendered


def test_conformance_summary_includes_phase_9_memory_manifest_cases() -> None:
    summary = build_conformance_summary()
    decisions = {
        decision["fixtureId"]: decision for decision in summary["decisions"]
    }

    for fixture_id in (
        "memory-failed-proposed",
        "memory-accepted-run-context",
        "memory-blocked-repo-write",
        "memory-superseded",
        "memory-source-identity",
        "memory-unsafe-content",
    ):
        assert decisions[fixture_id]["decision"] == decisions[fixture_id]["expected"]
        assert "DESIGN-REQ-007" in decisions[fixture_id]["traceability"]


def test_terminology_guardrail_is_part_of_conformance_entrypoint() -> None:
    summary = build_conformance_summary()

    assert (
        summary["terminologyGuardrail"]["tool"]
        == "tools/verify_workflow_terminology.py"
    )
    assert summary["terminologyGuardrail"]["mode"] == "runtime"
    assert summary["terminologyGuardrail"]["invokedByEntrypoint"] is True
