"""MM-464 report workflow rollout mapping tests."""

from __future__ import annotations

import pytest

from moonmind.workflows.temporal.artifacts import TemporalArtifactValidationError
from moonmind.workflows.temporal.report_artifacts import (
    REPORT_WORKFLOW_ROLLOUT_PHASES,
    build_report_bundle_result,
    build_report_projection_summary,
    classify_report_rollout_artifacts,
    get_report_workflow_mapping,
    validate_report_workflow_artifact_classes,
)

def test_supported_report_workflow_mappings_separate_report_and_observability() -> None:
    """MM-464: workflow examples map curated report classes separately."""

    unit_test = get_report_workflow_mapping("unit_test")
    assert unit_test.workflow_family == "unit_test"
    assert unit_test.report_type == "unit_test_report"
    assert unit_test.report_link_types == (
        "report.primary",
        "report.summary",
        "report.structured",
        "report.evidence",
    )
    assert unit_test.observability_link_types == (
        "runtime.stdout",
        "runtime.stderr",
        "runtime.diagnostics",
    )
    assert "finding_counts" in unit_test.recommended_metadata_keys

    coverage = get_report_workflow_mapping("coverage")
    assert coverage.report_link_types == (
        "report.primary",
        "report.structured",
        "report.evidence",
    )
    assert "runtime.stdout" not in coverage.report_link_types
    assert "finding_counts" in coverage.recommended_metadata_keys

    security = get_report_workflow_mapping("security_pentest")
    assert security.report_link_types == (
        "report.primary",
        "report.summary",
        "report.structured",
        "report.evidence",
    )
    assert "severity_counts" in security.recommended_metadata_keys
    assert "sensitivity" in security.recommended_metadata_keys

    benchmark = get_report_workflow_mapping("benchmark")
    assert benchmark.report_link_types == (
        "report.primary",
        "report.structured",
        "report.evidence",
    )
    assert "finding_counts" in benchmark.recommended_metadata_keys

def test_report_workflow_validation_requires_primary_report() -> None:
    """MM-464: new report-producing workflows cannot treat output.primary as a report."""

    with pytest.raises(TemporalArtifactValidationError, match="report.primary"):
        validate_report_workflow_artifact_classes(
            "unit_test",
            ("output.primary", "runtime.stdout", "runtime.diagnostics"),
        )

    with pytest.raises(TemporalArtifactValidationError, match="report.primary"):
        validate_report_workflow_artifact_classes(
            "coverage",
            ("report.structured", "report.evidence"),
        )

    validate_report_workflow_artifact_classes(
        "unit_test",
        (
            "report.primary",
            "report.summary",
            "report.structured",
            "runtime.stdout",
            "runtime.stderr",
            "runtime.diagnostics",
        ),
    )

def test_generic_output_only_artifacts_classify_as_rollout_fallback() -> None:
    """MM-464: generic outputs remain valid fallback, not canonical reports."""

    classification = classify_report_rollout_artifacts(
        ("output.primary", "output.summary", "runtime.diagnostics")
    )

    assert classification["mode"] == "generic_fallback"
    assert classification["has_canonical_report"] is False
    assert classification["generic_output_link_types"] == (
        "output.primary",
        "output.summary",
    )
    assert classification["report_link_types"] == ()

    invalid = classify_report_rollout_artifacts(("report.evidence", "output.primary"))
    assert invalid["mode"] == "invalid"
    assert invalid["has_canonical_report"] is False
    assert invalid["report_link_types"] == ("report.evidence",)

def test_report_workflow_generic_fallback_rejects_non_generic_links() -> None:
    """MM-464: fallback validation only accepts generic output classes."""

    validate_report_workflow_artifact_classes(
        "unit_test",
        ("output.primary", "output.summary"),
        allow_generic_fallback=True,
    )

    with pytest.raises(
        TemporalArtifactValidationError,
        match="unsupported generic fallback link type runtime.stdout",
    ):
        validate_report_workflow_artifact_classes(
            "unit_test",
            ("output.primary", "runtime.stdout"),
            allow_generic_fallback=True,
        )

def test_report_rollout_phases_are_ordered() -> None:
    """MM-464: incremental migration phases are available to runtime helpers."""

    assert REPORT_WORKFLOW_ROLLOUT_PHASES == (
        "metadata_conventions",
        "report_links_and_ui_surfacing",
        "compact_report_bundle_contract",
        "optional_projections_filters_retention_pinning",
    )

def test_report_projection_summary_is_ref_only() -> None:
    """MM-464: convenience summaries are projections over artifact refs."""

    bundle = build_report_bundle_result(
        primary_report_ref={"artifact_ref_v": 1, "artifact_id": "art_primary"},
        summary_ref={"artifact_ref_v": 1, "artifact_id": "art_summary"},
        evidence_refs=({"artifact_ref_v": 1, "artifact_id": "art_evidence"},),
        report_type="security_pentest_report",
        report_scope="final",
        counts={"high": 1},
    )

    summary = build_report_projection_summary(
        bundle,
        metadata={
            "finding_counts": {"total": 3},
            "severity_counts": {"high": 1},
        },
    )

    assert summary == {
        "has_report": True,
        "latest_report_ref": {"artifact_ref_v": 1, "artifact_id": "art_primary"},
        "latest_report_summary_ref": {
            "artifact_ref_v": 1,
            "artifact_id": "art_summary",
        },
        "report_type": "security_pentest_report",
        "report_status": "final",
        "finding_counts": {"total": 3},
        "severity_counts": {"high": 1},
    }

    with pytest.raises(TemporalArtifactValidationError, match="unsafe report bundle"):
        build_report_projection_summary(
            {
                "report_bundle_v": 1,
                "primary_report_ref": {
                    "artifact_ref_v": 1,
                    "artifact_id": "art_primary",
                },
                "report_body": "# Inline body",
            }
        )

    with pytest.raises(TemporalArtifactValidationError, match="unsafe report"):
        build_report_projection_summary(
            bundle,
            metadata={"raw_download_url": "https://example.invalid/report"},
        )
