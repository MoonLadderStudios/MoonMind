"""MM-908 advisory documentation-architecture validation helper tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[3] / "tools" / "check_documentation_architecture.py"
)
SPEC = importlib.util.spec_from_file_location(
    "check_documentation_architecture", MODULE_PATH
)
assert SPEC is not None
check_documentation_architecture = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = check_documentation_architecture
SPEC.loader.exec_module(check_documentation_architecture)

mod = check_documentation_architecture
DocFile = mod.DocFile


def _doc(path: str, text: str) -> "mod.DocFile":
    return DocFile(path=path, text=text)


def _rules(findings) -> set[str]:
    return {finding.rule for finding in findings}


def test_missing_document_class_flags_canonical_doc_without_marker() -> None:
    docs = [_doc("docs/NewThing.md", "# New Thing\n\nSome architecture prose.\n")]
    findings = mod.check_missing_document_class(docs)
    assert _rules(findings) == {"missing-document-class"}
    assert findings[0].severity == mod.SEVERITY_ADVISORY


def test_document_class_marker_satisfies_check() -> None:
    docs = [
        _doc(
            "docs/NewThing.md",
            "# New Thing\n\n**Document Class:** System Architecture View\n",
        )
    ]
    assert mod.check_missing_document_class(docs) == []


def test_inline_viewpoint_name_satisfies_document_class_check() -> None:
    docs = [
        _doc(
            "docs/NewThing.md",
            "# New Thing\n\nThis is a Cross-Cutting Concept View describing X.\n",
        )
    ]
    assert mod.check_missing_document_class(docs) == []


def test_non_canonical_dirs_are_exempt_from_document_class() -> None:
    docs = [
        _doc("docs/tmp/SomethingPlan.md", "# Plan\n\nstep 1\n"),
        _doc("docs/assets/note.md", "# Asset\n"),
        _doc("docs/ReleaseNotes/v1.md", "# Release\n"),
    ]
    assert mod.check_missing_document_class(docs) == []


def test_plan_in_canonical_area_is_flagged_but_tmp_is_allowed() -> None:
    docs = [
        _doc("docs/Workflows/BigMigrationPlan.md", "# Plan\n"),
        _doc("docs/Steps/RolloutPlan.md", "# Rollout\n"),
        _doc("docs/tmp/FeatureImplementationPlan.md", "# Plan\n"),
    ]
    findings = mod.check_imperative_plan_in_canonical_area(docs)
    flagged = {finding.path for finding in findings}
    assert flagged == {
        "docs/Workflows/BigMigrationPlan.md",
        "docs/Steps/RolloutPlan.md",
    }
    assert _rules(findings) == {"imperative-plan-in-canonical-area"}


def test_tracker_naming_is_flagged_in_canonical_area() -> None:
    docs = [_doc("docs/Temporal/StatusTracker.md", "# Status\n")]
    findings = mod.check_imperative_plan_in_canonical_area(docs)
    assert _rules(findings) == {"imperative-plan-in-canonical-area"}


def test_duplicate_titles_flag_overlapping_authority() -> None:
    docs = [
        _doc("docs/A/Overview.md", "# Execution Model\n\nx\n"),
        _doc("docs/B/Overview.md", "# Execution Model\n\ny\n"),
    ]
    findings = mod.check_duplicate_canonical_authority(docs)
    assert _rules(findings) == {"duplicate-canonical-authority"}
    assert {finding.path for finding in findings} == {
        "docs/A/Overview.md",
        "docs/B/Overview.md",
    }
    # The overlapping counterpart is recorded for the reviewer.
    assert any("docs/B/Overview.md" in finding.detail for finding in findings)


def test_multiple_system_architecture_views_flagged() -> None:
    docs = [
        _doc(
            "docs/MoonMindArchitecture.md",
            "# MoonMind Architecture\n\n**Document Class:** System Architecture View\n",
        ),
        _doc(
            "docs/OtherArchitecture.md",
            "# Other Architecture\n\n**Document Class:** System Architecture View\n",
        ),
    ]
    findings = mod.check_duplicate_canonical_authority(docs)
    assert _rules(findings) == {"duplicate-canonical-authority"}
    assert {finding.path for finding in findings} == {
        "docs/MoonMindArchitecture.md",
        "docs/OtherArchitecture.md",
    }


def test_single_system_architecture_view_is_fine() -> None:
    docs = [
        _doc(
            "docs/MoonMindArchitecture.md",
            "# MoonMind Architecture\n\n**Document Class:** System Architecture View\n",
        )
    ]
    assert mod.check_duplicate_canonical_authority(docs) == []


def test_documentation_architecture_standard_not_miscounted_as_system_view() -> None:
    # The standard names itself with an *Architecture.md path but is not a
    # System Architecture View; it must not collide with the real system view.
    docs = [
        _doc(
            "docs/MoonMindArchitecture.md",
            "# MoonMind Architecture\n\n**Document Class:** System Architecture View\n",
        ),
        _doc(
            "docs/DocumentationArchitecture.md",
            "# MoonSpec Documentation Architecture Standard\n\nTaxonomy of viewpoints.\n",
        ),
    ]
    assert mod.check_duplicate_canonical_authority(docs) == []


def test_contract_without_authority_statement_flagged() -> None:
    docs = [
        _doc("docs/Api/WidgetContract.md", "# Widget Contract\n\nFields: a, b.\n")
    ]
    findings = mod.check_contract_missing_authority_statement(docs)
    assert _rules(findings) == {"contract-missing-authority-statement"}


def test_contract_with_authority_statement_is_fine() -> None:
    docs = [
        _doc(
            "docs/Api/WidgetContract.md",
            "# Widget Contract\n\nThis is the authoritative source of truth, "
            "owned by the widget module.\n",
        )
    ]
    assert mod.check_contract_missing_authority_statement(docs) == []


def test_decision_record_directory_and_adr_file_are_discouraged() -> None:
    docs = [
        _doc("docs/decisions/0001-use-x.md", "# Decision\n"),
        _doc("docs/Workflows/adr-0002-pick-y.md", "# ADR\n"),
        _doc("docs/Workflows/Normal.md", "# Normal\n"),
    ]
    findings = mod.check_discouraged_decision_record(docs)
    assert _rules(findings) == {"discouraged-decision-record"}
    assert {finding.path for finding in findings} == {
        "docs/decisions/0001-use-x.md",
        "docs/Workflows/adr-0002-pick-y.md",
    }


def test_malformed_canonical_claim_id_heading_is_advisory() -> None:
    docs = [
        _doc(
            "docs/Workflows/Thing.md",
            "# Thing\n\n**Document Class:** Canonical declarative\n\n"
            "### DOC-REQ-1 Missing zero padding\n",
        )
    ]
    findings = mod.check_malformed_claim_ids(docs)
    assert _rules(findings) == {"malformed-claim-id"}
    assert findings[0].severity == mod.SEVERITY_ADVISORY


def test_claim_heading_prefix_without_number_is_malformed() -> None:
    docs = [
        _doc(
            "docs/Workflows/Thing.md",
            "# Thing\n\n**Document Class:** Canonical declarative\n\n"
            "### DOC-REQ Missing number\n",
        )
    ]
    findings = mod.check_malformed_claim_ids(docs)
    assert _rules(findings) == {"malformed-claim-id"}


def test_duplicate_canonical_claim_ids_are_advisory() -> None:
    docs = [
        _doc(
            "docs/A/Thing.md",
            "# Thing A\n\n**Document Class:** Canonical declarative\n\n"
            "### DOC-REQ-001 One claim\n",
        ),
        _doc(
            "docs/B/Thing.md",
            "# Thing B\n\n**Document Class:** Canonical declarative\n\n"
            "### DOC-REQ-001 Another claim\n",
        ),
    ]
    findings = mod.check_duplicate_claim_ids(docs)
    assert _rules(findings) == {"duplicate-claim-id"}
    assert {finding.path for finding in findings} == {
        "docs/A/Thing.md",
        "docs/B/Thing.md",
    }


def test_design_req_traceability_is_not_a_canonical_claim_id() -> None:
    docs = [
        _doc(
            "docs/Workflows/Thing.md",
            "# Thing\n\n**Document Class:** Canonical declarative\n\n"
            "Traceability: DESIGN-REQ-008\n\n"
            "### DOC-REQ-001 Stable claim\n",
        )
    ]
    assert mod.check_malformed_claim_ids(docs) == []
    assert mod.check_duplicate_claim_ids(docs) == []


def test_claim_ids_in_fenced_examples_are_not_validated() -> None:
    docs = [
        _doc(
            "docs/Workflows/Thing.md",
            "# Thing\n\n**Document Class:** Canonical declarative\n\n"
            "```markdown\n### DOC-REQ-1 Example only\n```\n",
        )
    ]
    assert mod.check_malformed_claim_ids(docs) == []


def test_clean_canonical_doc_produces_no_findings() -> None:
    docs = [
        _doc(
            "docs/Workflows/CleanContract.md",
            "# Clean Contract\n\n**Document Class:** Module Contract Specification\n\n"
            "This document is the authoritative source of truth, owned by the "
            "workflows module.\n\n"
            "### CONTRACT-001 Stable guarantee\n",
        )
    ]
    assert mod.run_checks(docs) == []


def test_focus_paths_reports_only_changed_doc_but_uses_full_context() -> None:
    # Regression: with the default ``changed`` scope, a new/changed doc that
    # duplicates the title of an *unchanged* canonical doc must still be flagged.
    # Global checks see the full canonical set (``docs``) while reporting is
    # scoped to the changed path via ``focus_paths``.
    changed = "docs/B/Overview.md"
    cls = "\n\nThis is a Cross-Cutting Concept View describing X.\n"
    docs = [
        _doc("docs/A/Overview.md", f"# Execution Model{cls}"),
        _doc(changed, f"# Execution Model{cls}"),
    ]
    findings = mod.run_checks(docs, focus_paths=[changed])
    assert _rules(findings) == {"duplicate-canonical-authority"}
    # Only the changed doc is reported; the unchanged counterpart is not flagged.
    assert {finding.path for finding in findings} == {changed}
    # The unchanged counterpart is still surfaced in the detail for the reviewer.
    assert any("docs/A/Overview.md" in finding.detail for finding in findings)


def test_focus_paths_none_reports_every_finding() -> None:
    docs = [
        _doc("docs/A/Overview.md", "# Execution Model\n\nx\n"),
        _doc("docs/B/Overview.md", "# Execution Model\n\ny\n"),
    ]
    findings = mod.run_checks(docs, focus_paths=None)
    assert {finding.path for finding in findings} == {
        "docs/A/Overview.md",
        "docs/B/Overview.md",
    }


def test_findings_are_sorted_deterministically() -> None:
    docs = [
        _doc("docs/Z/Zed.md", "# Zed\n"),
        _doc("docs/A/Ay.md", "# Ay\n"),
    ]
    findings = mod.run_checks(docs)
    paths = [finding.path for finding in findings]
    assert paths == sorted(paths)


def test_repo_doc_helpers_resolve_real_tree() -> None:
    # The shipped advisory docs/tool must exist and be classifiable.
    paths = mod.all_doc_paths()
    assert "docs/DocumentationArchitectureValidation.md" in paths
    assert mod.is_canonical_doc("docs/MoonMindArchitecture.md")
    assert not mod.is_canonical_doc("docs/tmp/SomePlan.md")


def test_shipped_validation_doc_declares_document_class() -> None:
    doc = mod.load_docs(["docs/DocumentationArchitectureValidation.md"])
    assert doc, "validation doc should load"
    assert mod.check_missing_document_class(doc) == []


def test_cli_json_output_is_advisory_and_exit_zero(capsys) -> None:
    rc = mod.main(["--format", "json", "docs/DocumentationArchitectureValidation.md"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert rc == 0
    assert payload["advisory_only"] is True
    assert payload["issue"] == "MM-908"
    # The shipped validation doc is clean, so it produces no findings.
    assert payload["finding_count"] == 0


def test_default_full_scan_is_non_blocking(capsys) -> None:
    # Advisory v1: a full-tree scan never blocks, even though existing docs have
    # not yet adopted the Document Class marker.
    rc = mod.main(["--scope", "all"])
    capsys.readouterr()
    assert rc == 0


def test_strict_mode_returns_nonzero_when_findings_exist(capsys) -> None:
    # The strict flag is the future-promotion path: it is opt-in and v1 CI must
    # not use it. With many docs not yet carrying the marker, a strict full scan
    # surfaces findings and exits non-zero.
    rc = mod.main(["--strict", "--scope", "all"])
    out = capsys.readouterr().out
    assert "advisory" in out.lower()
    assert rc == 1
