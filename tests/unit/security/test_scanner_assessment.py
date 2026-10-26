"""Comparison and bounds for retained repository scanner evidence."""

from datetime import UTC, datetime

import pytest

from moonmind.security.assessments import (
    AssessmentRequest,
    Finding,
    compare_observations,
)
from moonmind.security.repository_scanners import (
    NativeResult,
    RepositoryScan,
    ScannerExecution,
    ScannerSummary,
)
from moonmind.workflows.skills.security_assessment import _scanner_observation

NOW = datetime(2026, 10, 26, tzinfo=UTC)


def summary(scanner="opengrep", *, rule="native-rule", findings=True):
    options = {}
    if scanner == "opengrep":
        options["rules"] = [
            {
                "id": "selected-config",
                "path": "/opt/moonmind-scanner-rules/source.yaml",
                "sha256": "b" * 64,
                "license": "MIT",
            }
        ]
    elif scanner == "trivy":
        options["trivyScanners"] = ["misconfig"]
    admission = RepositoryScan(
        scanner=scanner, outputDirectory="artifacts/baseline", **options
    )
    refs = {mode: f"art_{mode}" for mode in admission.modes}
    return ScannerSummary(
        scanner=scanner,
        admission=admission,
        execution=ScannerExecution(
            inputDigest="a" * 64,
            assets=["app.py"],
            version="1.2.3",
            exits={mode: 0 for mode in admission.modes},
            states={mode: "succeeded" for mode in admission.modes},
        ),
        results={
            mode: NativeResult(
                execution="succeeded",
                coverage="complete",
                examinedAssets=["app.py"],
                findings=(
                    [
                        Finding(
                            source="repository",
                            tool=scanner,
                            ruleId=rule,
                            asset="app.py",
                            nativeId=mode,
                            evidenceRefs=[refs[mode]],
                        )
                    ]
                    if findings
                    else []
                ),
            )
            for mode in admission.modes
        },
        nativeRefs=refs,
        coverage="complete",
        limitations=[],
    )


def observe(retained):
    request = AssessmentRequest(
        scope={"assets": ["app.py"]},
        tools=["retained-report"],
        reportRefs=["art_summary"],
        credentialRefs=[],
        networkRefs=[],
        budget={"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
    )
    return _scanner_observation(request, retained, "art_summary", NOW)


def test_scanner_comparison_ignores_report_destinations():
    baseline = compare_observations(
        observe(summary(rule="selected-config")), None, now=NOW
    )
    current = summary(rule="selected-config", findings=False)
    current.admission.output_directory = "artifacts/current"
    result = compare_observations(observe(current), baseline, now=NOW)
    assert result["findings"][0]["state"] == "absent_comparable"


@pytest.mark.parametrize("scanner", ["gitleaks", "opengrep", "trivy"])
def test_clean_rescan_covers_native_rules_without_positive_findings(scanner):
    baseline = compare_observations(observe(summary(scanner)), None, now=NOW)
    result = compare_observations(
        observe(summary(scanner, findings=False)), baseline, now=NOW
    )
    assert {row["state"] for row in result["findings"]} == {"absent_comparable"}


@pytest.mark.parametrize("change", ["version", "rules", "partial", "unexamined"])
def test_scanner_comparison_keeps_findings_without_comparable_coverage(change):
    baseline = compare_observations(observe(summary()), None, now=NOW)
    current = summary(findings=False)
    if change == "version":
        current.execution.version = "1.2.4"
    elif change == "rules":
        current.admission.rules[0].sha256 = "c" * 64
    elif change == "partial":
        current.results["scan"].coverage = "partial"
    else:
        current.results["scan"].examined_assets = []
    result = compare_observations(observe(current), baseline, now=NOW)
    assert result["findings"][0]["state"] == "not_reassessed"


def test_combined_scanner_findings_are_bounded_and_keep_retained_evidence():
    retained = summary("gitleaks", findings=False)
    for mode, row in retained.results.items():
        row.findings = [
            Finding(
                source="repository",
                tool="gitleaks",
                ruleId="native-rule",
                asset="app.py",
                nativeId=f"{mode}-{index}",
                evidenceRefs=[retained.native_refs[mode]],
            )
            for index in range(2050)
        ]
    observation = observe(retained)
    assert len(observation.findings) == 4096
    assert observation.coverage[0].status == "partial"
    assert {ref for f in observation.findings for ref in f.evidence_refs} == {
        "art_history",
        "art_worktree",
    }
    assert "combined_finding_bound_exceeded" in observation.provenance.values()
