"""Declared packs compile only explicit objectives and safely retain intelligence."""

import json
from datetime import UTC, datetime

import pytest

from moonmind.security.assessments import AssessmentScope
from moonmind.security.framework_packs import (
    compare_pack_metadata,
    compile_framework_plan,
    load_declared_pack,
    retained_pack_metadata,
)
from moonmind.security.frameworks import EvidencePeriod

NOW = datetime(2026, 11, 7, tzinfo=UTC)


def payload():
    return {
        "id": "example",
        "edition": "2026",
        "revision": "1",
        "title": "Example",
        "source": {
            "url": "https://example.org",
            "section": "1",
            "reviewedOn": "2026-10-01",
        },
        "reviewedBy": "operator",
        "reviewedOn": "2026-10-01",
        "publicationStatus": "published",
        "publishedOn": "2026-01-01",
        "effectiveFrom": "2026-01-01",
        "provenance": "operator supplied",
        "license": "example license",
        "distribution": "redistributable",
        "requirements": [
            {
                "id": "R1",
                "title": "Secret title",
                "summary": "Original synthetic prose describing the example requirement.",
                "category": "security",
                "applicability": "All systems",
                "evidencePrompt": "Secret prompt",
                "objectiveBasis": "Explicit checks",
                "objectivesComplete": True,
                "objectives": [
                    {
                        "id": "O1",
                        "description": "Secret description",
                        "source": "scanner",
                        "tool": "scanner",
                        "ruleIds": ["rule"],
                    }
                ],
            }
        ],
    }


def load(data=None, **kwargs):
    return load_declared_pack(
        json.dumps(data or payload()).encode(),
        pack_id="example",
        edition="2026",
        revision=kwargs.get("revision", "1"),
    )


def compile(pack, **kwargs):
    return compile_framework_plan(
        pack,
        scope=AssessmentScope(assets=["app"]),
        period=EvidencePeriod(start="2026-10-01", end="2026-10-31"),
        choices=kwargs.get("choices", {}),
        now=NOW,
    )


def test_exact_revision_and_unique_requirements():
    with pytest.raises(ValueError, match="selection"):
        load(revision="2")
    data = payload()
    data["requirements"] *= 2
    with pytest.raises(ValueError, match="duplicate"):
        load(data)


def test_explicit_objectives_compile_and_scope_is_admitted():
    plan, diagnostics = compile(load())
    assert plan.requirements[0].objectives[0].rule_ids == ["rule"]
    assert plan.requirements[0].objectives_complete
    assert diagnostics == []
    with pytest.raises(ValueError, match="scope"):
        compile(load(), choices={"R1": {"systems": ["outside"]}})
    with pytest.raises(ValueError, match="unknown"):
        compile(load(), choices={"missing": {}})


@pytest.mark.parametrize(
    "change",
    [
        {"publicationStatus": "draft"},
        {"publicationStatus": "superseded"},
        {"effectiveFrom": "2027-01-01"},
        {"distribution": "prohibited"},
    ],
)
def test_nonoperative_packs_are_refused(change):
    data = payload()
    data.update(change)
    with pytest.raises(ValueError):
        compile(load(data))


def test_restricted_text_omitted_and_comparison_reports_intelligence():
    data = payload()
    data["distribution"] = "restricted"
    pack = load(data)
    plan, diagnostics = compile(pack)
    metadata = retained_pack_metadata(
        pack, artifact_ref="art_pack", sha256="a" * 64, now=NOW
    )
    assert "Secret" not in json.dumps([plan.model_dump(mode="json"), metadata])
    assert diagnostics
    newer = dict(metadata, revision="2")
    assert compare_pack_metadata(newer, metadata)
    assert compare_pack_metadata(metadata, metadata) == []


def test_mappings_validate_endpoints_and_never_add_objectives():
    data = payload()
    data["mappingTargets"] = [
        {
            "id": "external",
            "frameworkId": "other",
            "edition": "2025",
            "requirementIds": ["X"],
        }
    ]
    data["mappings"] = [
        {
            "id": "M1",
            "fromRequirementIds": ["R1"],
            "targetId": "external",
            "toRequirementIds": ["X"],
            "relation": "partial",
            "rationale": "Crosswalk only",
            "source": data["source"],
            "basis": "upstream",
        }
    ]
    data["requirements"][0]["objectives"] = []
    data["requirements"][0]["objectivesComplete"] = False
    plan, _ = compile(load(data))
    assert plan.requirements[0].objectives == []
    assert not plan.requirements[0].objectives_complete
    data["mappings"][0]["toRequirementIds"] = ["unknown"]
    with pytest.raises(ValueError, match="unknown"):
        load(data)


def pack_payload():
    """Shared synthetic pack for security.assess integration tests."""
    data = payload()
    data["id"] = "synthetic"
    row = data["requirements"][0]
    row["id"] = "control-1"
    row["objectives"][0].update(
        source="fixture-advisories", tool="inventory-feed", ruleIds=["CVE-EXAMPLE"]
    )
    return data


def test_ambiguous_json_and_target_editions_are_rejected():
    raw = json.dumps(payload()).replace(
        '"edition": "2026"', '"edition": "2025", "edition": "2026"'
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_declared_pack(
            raw.encode(), pack_id="example", edition="2026", revision="1"
        )
    data = payload()
    data["mappingTargets"] = [
        {
            "id": "first",
            "frameworkId": "other",
            "edition": "2025",
            "requirementIds": ["X"],
        },
        {
            "id": "second",
            "frameworkId": "other",
            "edition": "2025",
            "requirementIds": ["Y"],
        },
    ]
    with pytest.raises(ValueError, match="edition"):
        load(data)


def test_requirement_period_and_pack_bounds():
    with pytest.raises(ValueError, match="period"):
        compile(
            load(),
            choices={"R1": {"period": {"start": "2026-09-01", "end": "2026-10-31"}}},
        )
    data = payload()
    data["requirements"][0]["objectives"][0]["ruleIds"] *= 2
    with pytest.raises(ValueError, match="duplicate"):
        load(data)
    data = payload()
    data["requirements"] = [
        dict(data["requirements"][0], id=f"R{i}") for i in range(65)
    ]
    with pytest.raises(ValueError):
        load(data)


def test_material_changes_have_separate_intelligence_digests():
    original = retained_pack_metadata(
        load(), artifact_ref="art_pack", sha256="a" * 64, now=NOW
    )
    data = payload()
    data["requirements"][0]["objectives"][0]["ruleIds"] = ["changed"]
    updated = retained_pack_metadata(
        load(data), artifact_ref="art_other", sha256="b" * 64, now=NOW
    )
    assert updated["requirementsDigest"] != original["requirementsDigest"]
    assert updated["mappingsDigest"] == original["mappingsDigest"]
    assert any(
        "requirementsDigest" in item
        for item in compare_pack_metadata(updated, original)
    )
    assert all(
        "not remediation" in item for item in compare_pack_metadata(updated, original)
    )


@pytest.mark.parametrize(
    "field,value", [("edition", "latest"), ("revision", "current")]
)
def test_mutable_selectors_are_not_declared_versions(field, value):
    data = payload()
    data[field] = value
    with pytest.raises(ValueError, match="explicit"):
        load(data)


@pytest.mark.parametrize(
    "url", ["file:///private", "not-a-url", "https://user:password@example.org"]
)
def test_sources_require_safe_web_urls(url):
    data = payload()
    data["source"]["url"] = url
    with pytest.raises(ValueError, match="URL"):
        load(data)


def test_required_non_scanner_methods_withhold_completeness():
    data = payload()
    data["requirements"][0]["requiredEvidenceMethods"] = ["enquiry"]
    plan, diagnostics = compile(load(data))
    assert not plan.requirements[0].objectives_complete
    assert any("completeness withheld" in item for item in diagnostics)


def test_redistributable_metadata_preserves_readable_provenance_and_requirements():
    metadata = retained_pack_metadata(
        load(), artifact_ref="art_pack", sha256="a" * 64, now=NOW
    )
    assert metadata["license"] == "example license"
    assert metadata["source"]["url"] == "https://example.org"
    assert metadata["reviewedBy"] == "operator"
    assert metadata["requirements"][0]["title"] == "Secret title"


def test_applicability_requires_an_explicit_reasoned_choice():
    plan, _ = compile(load())
    assert plan.requirements[0].applicability == "undetermined"
    plan, _ = compile(
        load(),
        choices={
            "R1": {
                "applicability": "applicable",
                "applicabilityReason": "The admitted application processes the covered records.",
            }
        },
    )
    assert plan.requirements[0].applicability == "applicable"
    assert (
        plan.requirements[0].applicability_reason
        == "The admitted application processes the covered records."
    )
    with pytest.raises(ValueError, match="reason"):
        compile(load(), choices={"R1": {"applicability": "applicable"}})
    plan, _ = compile(
        load(),
        choices={"R1": {"notApplicableReason": "No covered records are processed."}},
    )
    assert plan.requirements[0].applicability == "not_applicable"
    assert (
        plan.requirements[0].applicability_reason == "No covered records are processed."
    )


def test_conflicting_applicability_cannot_silently_exclude_requirement():
    with pytest.raises(ValueError):
        compile(
            load(),
            choices={
                "R1": {
                    "applicability": "applicable",
                    "applicabilityReason": "Applies",
                    "notApplicableReason": "Does not apply",
                }
            },
        )


def test_original_requirement_summary_is_required():
    data = payload()
    data["requirements"][0].pop("summary", None)
    with pytest.raises(ValueError, match="summary"):
        load(data)


def test_amendment_only_change_is_assessment_intelligence():
    original = retained_pack_metadata(
        load(), artifact_ref="art_pack", sha256="a" * 64, now=NOW
    )
    data = payload()
    data["amendments"] = ["amendment-2026-1"]
    changed = retained_pack_metadata(
        load(data), artifact_ref="art_pack", sha256="a" * 64, now=NOW
    )
    assert changed["amendments"] == ["amendment-2026-1"]
    assert compare_pack_metadata(changed, original) == [
        "Framework assessment intelligence changed: amendments; this is not remediation evidence."
    ]


def test_amendments_are_bounded_and_unique():
    data = payload()
    data["amendments"] = ["same", "same"]
    with pytest.raises(ValueError, match="duplicate"):
        load(data)
    data["amendments"] = [f"amendment-{index}" for index in range(33)]
    with pytest.raises(ValueError):
        load(data)


def test_partner_responsibility_reason_is_retained_in_assessment_plan():
    plan, _ = compile(
        load(),
        choices={
            "R1": {
                "responsibleEntity": "partner",
                "responsibilityReason": "The contracted operator controls the assessed system.",
            }
        },
    )
    row = plan.model_dump(mode="json", by_alias=True)["requirements"][0]
    assert row["owner"] == "partner"
    assert (
        row["responsibilityReason"]
        == "The contracted operator controls the assessed system."
    )


def test_many_to_many_conflicting_mapping_claims_remain_attributed_intelligence():
    data = payload()
    data["requirements"].append(dict(data["requirements"][0], id="R2"))
    data["mappingTargets"] = [
        {
            "id": "external",
            "frameworkId": "other",
            "edition": "2025",
            "requirementIds": ["X", "Y"],
        }
    ]
    upstream = {
        "id": "upstream",
        "fromRequirementIds": ["R1", "R2"],
        "targetId": "external",
        "toRequirementIds": ["X", "Y"],
        "relation": "equivalent",
        "rationale": "Vendor interpretation",
        "source": data["source"],
        "basis": "upstream",
    }
    data["mappings"] = [
        upstream,
        dict(
            upstream,
            id="assessor",
            relation="partial",
            basis="evaluated",
            reviewedBy="Fixture assessor",
            reviewedOn="2026-10-01",
            rationale="The populations overlap only partially.",
        ),
    ]
    pack = load(data)
    metadata = retained_pack_metadata(
        pack, artifact_ref="art_pack", sha256="a" * 64, now=NOW
    )
    assert [row["relation"] for row in metadata["mappings"]] == [
        "equivalent",
        "partial",
    ]
    assert [row["basis"] for row in metadata["mappings"]] == ["upstream", "evaluated"]
    plan, diagnostics = compile(pack)
    assert len(plan.requirements) == 2
    assert all(len(row.objectives) == 1 for row in plan.requirements)
    assert all(row.applicability == "undetermined" for row in plan.requirements)
    assert any("provide no executed checks" in row for row in diagnostics)
    data["mappings"][0]["relation"] = "related"
    changed = retained_pack_metadata(
        load(data), artifact_ref="art_new", sha256="b" * 64, now=NOW
    )
    assert any(
        "mappingsDigest" in row for row in compare_pack_metadata(changed, metadata)
    )
    assert changed["requirementsDigest"] == metadata["requirementsDigest"]


def test_effective_date_transition_never_substitutes_an_edition():
    from datetime import timedelta

    data = payload()
    data["effectiveFrom"] = NOW.date().isoformat()
    data["effectiveUntil"] = (NOW.date() + timedelta(days=1)).isoformat()
    pack = load(data)
    for now, succeeds in [
        (NOW - timedelta(days=1), False),
        (NOW, True),
        (NOW + timedelta(days=1), False),
    ]:
        if succeeds:
            assert (
                retained_pack_metadata(
                    pack, artifact_ref="art_pack", sha256="a" * 64, now=now
                )["edition"]
                == "2026"
            )
        else:
            with pytest.raises(ValueError):
                retained_pack_metadata(
                    pack, artifact_ref="art_pack", sha256="a" * 64, now=now
                )
