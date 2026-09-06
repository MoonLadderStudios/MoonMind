"""Repository access release-gate harness tests (MoonLadderStudios/MoonMind#4024).

Fast deterministic regressions for the final integration/release gate. These
prove gate discipline in the required unit shard: frozen-revision mapping,
bounded risk matrix, run-provenance tier separation, selector collection, and
aggregate failure integrity. They do not claim credential, capture,
publication, or clean-install conformance, which remain unimplemented/blocked
until the owning issues land.
"""

from __future__ import annotations

import json
from pathlib import Path

from moonmind.workflows.repository_access_gate import (
    CONSUMER_BOUNDARIES,
    FROZEN_DESIGN_REVISION,
    REQUIRED_CAPABILITIES,
    STABLE_CLAIM_IDS,
    ClaimCoverage,
    ConsumerCoverage,
    GateScenarioResult,
    RiskCombination,
    RunProvenance,
    aggregate_gate_report,
    reject_scope_relabel,
    validate_claim_coverage,
    validate_consumer_coverage,
    validate_risk_matrix,
    validate_run_provenance,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "repository_access_gate"
    / "coverage_matrix.json"
)


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def _claim_coverages(payload: dict) -> list[ClaimCoverage]:
    return [ClaimCoverage(**row) for row in payload["claims"]]


def _consumer_coverages(payload: dict) -> list[ConsumerCoverage]:
    return [ConsumerCoverage(**row) for row in payload["consumers"]]


def _provenance(**overrides: object) -> RunProvenance:
    base: dict[str, object] = {
        "source_digest": "sha256:source",
        "build_digest": "sha256:build",
        "architecture": "linux/amd64",
        "schema_versions": {"managed_session": "v1"},
        "policy_versions": {"repository-access": "v1"},
        "db_engine": "postgres",
        "db_version": "16",
        "artifact_backend": "s3",
        "topology": "worker+temporal",
        "provider_adapter": "github-pat",
        "scenario": "scratch report without PAT",
        "substitutions": (),
        "execution_tier": "real-postgres-object-store",
    }
    base.update(overrides)
    return RunProvenance(**base)  # type: ignore[arg-type]


def test_stable_claim_baseline_has_42_ids() -> None:
    assert len(STABLE_CLAIM_IDS) == 42
    assert "TEST-001" in STABLE_CLAIM_IDS
    assert "NON-GOAL-001" in STABLE_CLAIM_IDS
    assert "QUALITY-009" in STABLE_CLAIM_IDS


def test_fixture_freezes_reviewed_revision() -> None:
    payload = _load_fixture()
    assert payload["frozen_design_revision"] == FROZEN_DESIGN_REVISION
    assert payload["frozen_design_revision"] == "1dffb29c28de06775ee6028305222c73d911628b"
    assert len(payload["claims"]) == 42
    assert {row["claim_id"] for row in payload["claims"]} == set(STABLE_CLAIM_IDS)


def test_fixture_covers_every_consumer_boundary() -> None:
    payload = _load_fixture()
    assert {row["boundary"] for row in payload["consumers"]} == set(
        CONSUMER_BOUNDARIES
    )
    assert len(payload["consumers"]) == 9


def test_claim_coverage_requires_owner_entrypoint_evidence() -> None:
    coverages = _claim_coverages(_load_fixture())
    errors = validate_claim_coverage(
        coverages, design_revision=FROZEN_DESIGN_REVISION
    )
    assert errors == []


def test_claim_coverage_detects_missing_claim() -> None:
    coverages = [
        c for c in _claim_coverages(_load_fixture()) if c.claim_id != "INV-001"
    ]
    errors = validate_claim_coverage(
        coverages, design_revision=FROZEN_DESIGN_REVISION
    )
    assert any("INV-001" in e for e in errors)


def test_design_revision_change_requires_coverage_delta_review() -> None:
    coverages = _claim_coverages(_load_fixture())
    errors = validate_claim_coverage(coverages, design_revision="deadbeef")
    assert any("coverage delta" in e for e in errors)


def test_consumer_coverage_requires_owner_entrypoint_evidence() -> None:
    errors = validate_consumer_coverage(_consumer_coverages(_load_fixture()))
    assert errors == []


def test_required_capabilities_cannot_be_relabeled_unsupported() -> None:
    for capability in REQUIRED_CAPABILITIES:
        reason = reject_scope_relabel(capability, "unsupported-design-authorized")
        assert reason is not None
        assert "cannot be relabeled" in reason
    assert reject_scope_relabel("app-acquisition", "supported") is None
    assert reject_scope_relabel("app-acquisition", "unimplemented") is None


def test_unsupported_claim_needs_design_authorization() -> None:
    coverages = [
        ClaimCoverage(
            claim_id="CONTRACT-010",
            owner="runtime-delivery",
            test_entrypoint="entry",
            support="unsupported-design-authorized",
            evidence_artifact="artifact",
            design_authorization_ref="",
        )
    ]
    errors = validate_claim_coverage(
        coverages, design_revision=FROZEN_DESIGN_REVISION
    )
    assert any("authorization reference" in e for e in errors)
    # NON-GOAL-001 is the design-authorized out-of-scope example and passes.
    payload = _load_fixture()
    nongoal = next(
        c for c in _claim_coverages(payload) if c.claim_id == "NON-GOAL-001"
    )
    assert nongoal.support == "unsupported-design-authorized"
    assert nongoal.design_authorization_ref != ""


def _risk_fixture_combos(payload: dict) -> list[RiskCombination]:
    return [RiskCombination(**row) for row in payload["risk_matrix"]]


def test_risk_matrix_advertised_combination_needs_positive_journey() -> None:
    combos = [
        RiskCombination(
            combination_id="combo-a",
            runtime="opencode",
            source="scratch",
            access_mode="none",
            output_publication="report",
            positive_journey="",
            negative_recovery_owner="workspace-preparation",
        )
    ]
    errors = validate_risk_matrix(combos, authority_handoffs=["admission"])
    assert any("positive production journey" in e for e in errors)


def test_risk_matrix_advertised_combination_needs_recovery_owner() -> None:
    combos = [
        RiskCombination(
            combination_id="combo-a",
            runtime="opencode",
            source="scratch",
            access_mode="none",
            output_publication="report",
            positive_journey="journey",
            negative_recovery_owner="",
        )
    ]
    errors = validate_risk_matrix(combos, authority_handoffs=[])
    assert any("negative/recovery owner" in e for e in errors)


def test_risk_matrix_shared_substrate_needs_named_implementation() -> None:
    combos = [
        RiskCombination(
            combination_id="combo-a",
            runtime="opencode",
            source="scratch",
            access_mode="none",
            output_publication="report",
            positive_journey="journey",
            negative_recovery_owner="owner",
            shared_substrate_justification="shared",
        )
    ]
    errors = validate_risk_matrix(combos, authority_handoffs=[])
    assert any("shared-substrate" in e for e in errors)


def test_risk_matrix_handoff_without_owner_is_rejected() -> None:
    combos = [
        RiskCombination(
            combination_id="combo-a",
            runtime="opencode",
            source="scratch",
            access_mode="none",
            output_publication="report",
            positive_journey="journey",
            negative_recovery_owner="workspace",
        )
    ]
    errors = validate_risk_matrix(combos, authority_handoffs=["publication"])
    assert any("publication" in e for e in errors)


def test_fixture_risk_matrix_names_handoffs_truthfully() -> None:
    payload = _load_fixture()
    # Fixture risk rows are still pending journeys, but the validator must see
    # the structural requirement: rows carry both journey and recovery owner
    # placeholders rather than empty fields that would silently pass.
    for row in payload["risk_matrix"]:
        assert row["positive_journey"] != ""
        assert row["negative_recovery_owner"] != ""
    combos = _risk_fixture_combos(payload)
    errors = validate_risk_matrix(
        combos, authority_handoffs=payload["authority_handoffs"]
    )
    assert errors == []


def test_run_provenance_rejects_postgres_name_on_sqlite() -> None:
    provenance = _provenance(
        scenario="PostgreSQL race coverage",
        db_engine="sqlite",
        execution_tier="real-local-process",
    )
    errors = validate_run_provenance(provenance)
    assert any("SQLite" in e for e in errors)


def test_run_provenance_rejects_mock_replay() -> None:
    provenance = _provenance(
        scenario="workflow replay",
        execution_tier="workflow-dispatch-replay",
        substitutions=("patch-marker",),
    )
    errors = validate_run_provenance(provenance)
    assert any("recorded-history replay" in e for e in errors)


def test_run_provenance_rejects_browser_fixture_as_live() -> None:
    provenance = _provenance(
        scenario="live enrollment",
        execution_tier="live-verification",
        substitutions=("browser-fixture",),
    )
    errors = validate_run_provenance(provenance)
    assert any("live enrollment" in e for e in errors)


def test_run_provenance_accepts_honest_tier_labels() -> None:
    assert validate_run_provenance(_provenance()) == []
    helper = _provenance(
        scenario="scratch report",
        db_engine="memory",
        artifact_backend="memory",
        execution_tier="helper-in-memory",
    )
    assert validate_run_provenance(helper) == []


def test_aggregate_report_never_turns_partial_evidence_into_success() -> None:
    for status in ("failed", "skipped", "missing-artifact", "incomplete"):
        report = aggregate_gate_report(
            [
                GateScenarioResult("a", "passed"),
                GateScenarioResult("b", status, "detail"),  # type: ignore[arg-type]
            ]
        )
        assert report.verdict in ("blocked", "failed")
        assert report.reasons != ()


def test_aggregate_report_empty_run_is_blocked() -> None:
    report = aggregate_gate_report([])
    assert report.verdict == "blocked"


def test_aggregate_report_all_passed_is_pass() -> None:
    report = aggregate_gate_report(
        [GateScenarioResult("a", "passed"), GateScenarioResult("b", "passed")]
    )
    assert report.verdict == "pass"


def test_selector_gate_module_selects_required_fast_shard() -> None:
    from tools.select_test_suites import select_suites

    outputs = select_suites(
        ["moonmind/workflows/repository_access_gate.py"],
        event_name="pull_request",
    ).as_outputs()
    # The gate module is backend code under moonmind/; its fast deterministic
    # regressions must be collected by the required unit shard.
    assert outputs["unit_fast"] == "true"
    assert outputs["full_backend"] == "false"


def test_selector_unknown_change_selects_full_verification() -> None:
    from tools.select_test_suites import select_suites

    outputs = select_suites(
        ["new_runtime_backend/worker.py"],
        event_name="pull_request",
    ).as_outputs()
    # Unknown changes fail open to full backend verification rather than
    # silently running only the fast shard.
    assert outputs["full_backend"] == "true"


def test_selector_docs_only_change_selects_no_backend() -> None:
    from tools.select_test_suites import select_suites

    outputs = select_suites(
        ["docs/RepositoryAccessAndWorkspaceDesign.md"],
        event_name="pull_request",
    ).as_outputs()
    assert outputs["unit_fast"] == "false"
    assert outputs["full_backend"] == "false"
