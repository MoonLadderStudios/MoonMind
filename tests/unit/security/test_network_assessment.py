"""Network exports in security.assess: freshness, inventory contradictions and
review limits, through the control evaluator (#5441)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from moonmind.schemas.repository_scanner_models import NetworkConfigAssessment
from moonmind.security.assessments import (
    AssessmentScope,
    Comparison,
    ControlSupportingEvidence,
    compare_observations,
)
from moonmind.security.control_assessments import assess_controls
from moonmind.security.framework_packs import compile_framework_plan
from moonmind.security.framework_registry import installed_pack
from moonmind.security.frameworks import EvidencePeriod
from moonmind.security.network_configs import (
    prepare_exports,
    summarize_network_config,
    verify_retained_summary,
)
from moonmind.workflows.skills.security_assessment import (
    _compose_network_evidence,
    _network_config_observation,
    _network_summary_lines,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "security" / "network_config"


def summary(name="corrected"):
    data = json.loads((FIXTURES / "assessments.json").read_text())[name]
    settings = NetworkConfigAssessment.model_validate(data)
    prepared = prepare_exports(
        settings,
        {e.file_name: (FIXTURES / e.file_name).read_bytes() for e in settings.exports},
    )
    native = json.loads((FIXTURES / f"native-{name}.json").read_text())
    refs = {
        file: f"art_sanitized{index}" for index, file in enumerate(prepared.sanitized)
    }
    return (
        summarize_network_config(
            settings, prepared, native, native_ref="art_native", sanitized_refs=refs
        ),
        prepared,
        native,
    )


def assess(observation, *, now, choices):
    pack = installed_pack("network-best-practices")[1]
    assets = sorted({asset for row in observation.coverage for asset in row.assets})
    plan, _ = compile_framework_plan(
        pack,
        scope=AssessmentScope(assets=assets),
        period=EvidencePeriod(start=now.date(), end=now.date()),
        choices=choices,
        now=now,
    )
    compared = Comparison.model_validate(
        compare_observations(observation, None, now=now)
    )
    windows = [
        w.model_dump(mode="json", by_alias=True) for w in observation.coverage_windows
    ]
    report = assess_controls(
        plan,
        compared,
        scan_ref="art_scan",
        comparison_ref="art_comparison",
        coverage_windows=windows,
        now=now,
        evidence_availability={},
    )
    return {row.requirement.id: row for row in report.requirements}


def applicable(*ids):
    return {
        rid: {
            "applicability": "applicable",
            "applicabilityReason": "Edge export in scope.",
        }
        for rid in ids
    }


def observed(result):
    request = SimpleNamespace(
        scope=AssessmentScope(assets=[row.artifact_ref for row in result.files])
    )
    return _network_config_observation(request, result, "art_summary")


def test_a_retained_summary_is_recomputed_from_its_evidence():
    result, prepared, native = summary()
    assert verify_retained_summary(result, native, prepared.sanitized) == result
    weakened, weak_prepared, weak_native = summary("weakened")
    forged = weakened.model_copy(update={"findings": []})
    try:
        verify_retained_summary(forged, weak_native, weak_prepared.sanitized)
    except ValueError:
        pass
    else:
        raise AssertionError("a summary that drops findings must be refused")


def test_exports_conclude_as_of_their_export_time_and_go_stale():
    result, _, _ = summary()
    observation = observed(result)
    exported = result.files[0].exported_at
    now = exported + timedelta(hours=1)
    rows = assess(observation, now=now, choices=applicable("NET-1", "NET-2", "NET-3"))
    assert {rows[rid].conclusion for rid in ("NET-1", "NET-2", "NET-3")} == {
        "satisfied"
    }
    # The same export, past its freshness window, no longer evidences anything.
    stale = now + timedelta(days=91)
    rows = assess(observation, now=stale, choices=applicable("NET-1"))
    assert rows["NET-1"].conclusion != "satisfied"
    assert rows["NET-1"].evidence_sufficiency in {"stale", "insufficient"}


def test_weakened_export_fails_the_requirements_its_findings_name():
    result, _, _ = summary("weakened")
    edge = [row for row in result.files if row.file_name == "edge-weakened.cfg"]
    observation = observed(result)
    rows = assess(
        observation,
        now=edge[0].exported_at + timedelta(hours=1),
        choices=applicable("NET-1", "NET-2", "NET-3", "NET-4", "NET-5"),
    )
    assert {
        rows[rid].conclusion for rid in ("NET-1", "NET-2", "NET-3", "NET-4", "NET-5")
    } == {"not_satisfied"}


def test_inventory_contradiction_is_reported_and_leaves_the_requirement_inconclusive():
    result, _, _ = summary()
    observations = [observed(result)]
    entry = {
        "scanner": "batfish",
        "limitations": [],
        "files": [],
        "gaps": [],
        "sensitivity": result.sensitivity,
        "provenance": result.provenance,
        "qualification": result.qualification,
    }
    inventory = SimpleNamespace(
        endpoints=[SimpleNamespace(authority="10.10.10.20:23", reachability="open")]
    )
    _compose_network_evidence(
        [("art_summary", result, entry)], [inventory], observations
    )
    [comparison] = entry["comparisons"]
    assert comparison["relation"] == "contradiction"
    assert "contradicted_by_service_inventory" in entry["limitations"]
    assert {row.status for row in observations[0].coverage} == {"partial"}
    text = "\n".join(_network_summary_lines(entry))
    assert "Contradiction with the service inventory at 10.10.10.20:23" in text
    assert "inconclusive" in text
    now = result.files[0].exported_at + timedelta(hours=1)
    rows = assess(observations[0], now=now, choices=applicable("NET-1", "NET-2"))
    assert {rows["NET-1"].conclusion, rows["NET-2"].conclusion} == {"inconclusive"}


def test_review_interpretation_cannot_satisfy_a_check_or_a_record():
    result, prepared, _ = summary()
    # The analyzer produced nothing: only an agent interpretation remains.
    failed = summarize_network_config(
        result.settings,
        prepared,
        None,
        native_ref="unavailable",
        sanitized_refs={
            f.file_name: f.sanitized_ref for f in result.files if f.sanitized_ref
        },
    )
    observation = observed(failed)
    export = failed.files[0].artifact_ref
    now = failed.files[0].exported_at + timedelta(hours=1)
    period = EvidencePeriod(start=now.date(), end=now.date())
    plan, _ = compile_framework_plan(
        installed_pack("network-best-practices")[1],
        scope=AssessmentScope(assets=[export]),
        period=period,
        choices=applicable("NET-1", "NET-11"),
        now=now,
    )
    interpretation = ControlSupportingEvidence(
        artifact_ref="art_review",
        method="agent_interpretation",
        position="supports",
        objective_ids=["acl-line-unreachable"],
        scope=AssessmentScope(assets=[export]),
        period=period,
        author="security-review",
        reason="The filters at edge-corrected.cfg:21-22 look intended.",
    )
    plan = plan.model_copy(
        update={
            "requirements": [
                (
                    row.model_copy(update={"supporting_evidence": [interpretation]})
                    if row.id == "NET-1"
                    else row
                )
                for row in plan.requirements
            ]
        }
    )
    # A record requirement has no check for an interpretation to support.
    record = next(row for row in plan.requirements if row.id == "NET-11")
    with pytest.raises(ValidationError):
        type(record).model_validate(
            {
                **record.model_dump(by_alias=True),
                "supportingEvidence": [interpretation.model_dump(by_alias=True)],
            }
        )
    report = assess_controls(
        plan,
        Comparison.model_validate(compare_observations(observation, None, now=now)),
        scan_ref="art_scan",
        comparison_ref="art_comparison",
        coverage_windows=[
            w.model_dump(mode="json", by_alias=True)
            for w in observation.coverage_windows
        ],
        now=now,
        evidence_availability={"art_review": None},
    )
    rows = {row.requirement.id: row for row in report.requirements}
    assert "agent_interpretation" in rows["NET-1"].methods
    assert rows["NET-1"].conclusion != "satisfied"
    assert rows["NET-11"].conclusion != "satisfied"
