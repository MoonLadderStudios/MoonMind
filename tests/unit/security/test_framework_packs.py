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
    from moonmind.security.frameworks import MAX_PACK_REQUIREMENTS

    data["requirements"] = [
        dict(data["requirements"][0], id=f"R{i}") for i in range(MAX_PACK_REQUIREMENTS)
    ]
    assert len(load(data).requirements) == MAX_PACK_REQUIREMENTS
    data["requirements"].append(dict(data["requirements"][0], id="overflow"))
    with pytest.raises(ValueError, match="requirements"):
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


@pytest.mark.parametrize("length", [1024, 1025])
def test_declared_requirement_title_matches_evaluator_text_bound(length):
    data = payload()
    data["requirements"][0]["title"] = "x" * length
    if length > 1024:
        with pytest.raises(ValueError, match="title"):
            load(data)
    else:
        plan, _ = compile(load(data))
        assert plan.requirements[0].title == "x" * length


@pytest.mark.parametrize(
    "field", ["applicabilityReason", "notApplicableReason", "responsibilityReason"]
)
@pytest.mark.parametrize("length", [1024, 1025])
def test_declared_choice_reasons_match_evaluator_text_bound(field, length):
    from moonmind.security.frameworks import DeclaredRequirementChoice

    choice = {field: "x" * length}
    if length > 1024:
        with pytest.raises(ValueError):
            DeclaredRequirementChoice.model_validate(choice)
    else:
        admitted = DeclaredRequirementChoice.model_validate(choice)
        plan, _ = compile(load(), choices={"R1": admitted})
        row = plan.requirements[0]
        assert (
            row.responsibility_reason
            if field == "responsibilityReason"
            else row.applicability_reason
        ) == "x" * length


def test_legacy_framework_text_bounds_are_unchanged():
    from moonmind.security.frameworks import FrameworkRequirement, RequirementChoice

    requirement = payload()["requirements"][0]
    for field in ["objectives", "objectivesComplete", "objectiveBasis"]:
        requirement.pop(field)
    requirement["title"] = "x" * 4096
    assert len(FrameworkRequirement.model_validate(requirement).title) == 4096
    choice = RequirementChoice(
        not_applicable_reason="x" * 4096, responsibility_reason="x" * 4096
    )
    assert len(choice.not_applicable_reason) == 4096
    assert len(choice.responsibility_reason) == 4096


def test_declared_pack_preserves_requirement_local_organisational_requests():
    from tests.unit.security.test_control_assessments import organisational_request

    evidence = organisational_request()
    plan, _ = compile(load(), choices={"R1": {"organisationalEvidence": [evidence]}})
    assert plan.requirements[0].organisational_evidence[0].id == "policy"
    assert plan.evidence_refs() == ["art_policy"]
    assert plan.requirements[0].organisational_evidence[0].revisions[0].review.purpose == "design"


def test_profile_extension_leaves_existing_pack_digests_unchanged():
    # Captured from the declared contract before profile packs existed (#5435).
    metadata = retained_pack_metadata(
        load(), artifact_ref="art_pack", sha256="0" * 64, now=NOW
    )
    assert {
        key: metadata[key]
        for key in ("requirementsDigest", "mappingsDigest", "provenanceDigest")
    } == {
        "requirementsDigest": "1f84a7efdb88af71fdf29c43f51e2d9e63b2a303f3d5a62b30afeca7615c5658",
        "mappingsDigest": "643d5437104296e21d906ecb15b2c96ad278f20cfc4af53b12bb6069bd853726",
        "provenanceDigest": "3829a19e97225532459e0486144524db5f76b767ef5b4176c6bf00bc04e479d9",
    }
    assert "profileDigest" not in metadata
    dumped = load().model_dump(mode="json", by_alias=True)
    assert not {"profile", "modules", "behavior", "externalClaims"} & set(dumped)


def profile_payload():
    data = payload()
    data["profile"] = {
        "requestSource": "https://example.org",
        "fields": [
            {
                "name": "entityType",
                "type": "choice",
                "choices": ["operator", "supplier"],
                "unknownReason": "Record the entity type.",
            },
            {"name": "systems", "type": "text_list", "role": "systems"},
        ],
    }
    data["applicabilityText"] = {
        "applicable": "Applies; basis:",
        "basisMissing": "missing",
        "notApplicable": "Out of scope.",
        "notSelected": "Not selected.",
    }
    row = data["requirements"][0]
    row.update(objectives=[], objectivesComplete=False, evidenceTypes=["organisational"])
    row["applicableWhen"] = [{"entityType": "operator"}]
    return data


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d: d["profile"]["fields"][0].update(required=True), "request ID"),
        (lambda d: d["profile"]["fields"][0].update(choices=[]), "list choices"),
        (
            lambda d: d["requirements"][0].update(applicableWhen=[{"missing": True}]),
            "no boolean or choice",
        ),
        (
            lambda d: d["requirements"][0].update(applicableWhen=[{"entityType": "x"}]),
            "unknown choice",
        ),
        (lambda d: d.pop("applicabilityText"), "applicabilityText"),
        (
            lambda d: d["profile"]["fields"].append(
                {"name": "rules", "type": "choices", "choices": ["R9"], "role": "selection"}
            ),
            "selection names unknown",
        ),
        (
            lambda d: (
                d.pop("profile"),
                d.pop("applicabilityText"),
                d["requirements"][0].pop("applicableWhen"),
                d.update(behavior="x"),
            ),
            "profile inputs",
        ),
        (
            lambda d: d.update(
                modules=[
                    {
                        "id": "M1",
                        "edition": "1",
                        "source": d["source"],
                        "sourceStatus": "Unverified",
                    }
                ]
            ),
            "reason",
        ),
        (
            lambda d: d.update(
                modules=[{"id": "M1", "edition": "1", "source": d["source"]}]
            ),
            "undeclared module",
        ),
    ],
)
def test_profile_contract_rejects_inconsistent_declarations(change, message):
    from pydantic import ValidationError

    load(profile_payload())
    data = profile_payload()
    change(data)
    with pytest.raises((ValueError, ValidationError), match=message):
        load(data)


def test_declared_profile_rules_drive_applicability_and_refuse_guesses():
    from moonmind.security.pack_readiness import DEFAULT_BEHAVIOR, evaluate_readiness

    pack = load(profile_payload())

    def status(profile):
        return evaluate_readiness(
            pack,
            DEFAULT_BEHAVIOR,
            profile=profile,
            period=EvidencePeriod(start="2026-10-01", end="2026-10-31"),
            choices={},
            records=[],
            audit=None,
            admitted_assets=["app"],
        ).rows["R1"]

    assert status({"entityType": "operator", "systems": ["app"]})["status"] == "incomplete"
    assert status({"entityType": "supplier", "systems": ["app"]})["status"] == "not_applicable"
    unknown = status({"systems": ["app"]})
    assert (unknown["status"], unknown["reason"]) == ("Unverified", "Record the entity type.")
    # A profile pack compiles only from its readiness result.
    with pytest.raises(ValueError, match="readiness"):
        compile(pack)


def test_unregistered_behavior_is_refused():
    from moonmind.security.framework_registry import pack_behavior

    data = profile_payload()
    data["behavior"] = "not-registered"
    with pytest.raises(ValueError, match="unregistered behavior"):
        pack_behavior(load(data))


def legacy_request(pack_id, framework):
    return {
        "scope": {"assets": ["casino"]},
        "tools": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "credentialRefs": [],
        "networkRefs": [],
        "framework": {"packId": pack_id, **framework},
    }


def test_saved_framework_pack_id_normalizes_once_at_the_request_boundary():
    from moonmind.security.assessments import AssessmentRequest
    from moonmind.security.framework_registry import installed_pack

    framework = {
        "systemInputs": {"entityName": "Saved", "systems": ["casino"]},
        "period": {"start": "2026-11-04", "end": "2026-11-07"},
        "requirementChoices": {"GLI-GSF-2:B": {"systems": ["casino"]}},
        "evidenceRefs": ["art_saved_record"],
        "previousAssessmentRef": "art_previous",
    }
    request = AssessmentRequest.model_validate(
        legacy_request("gli-19-readiness", framework)
    )
    assert request.framework is None
    [selection] = request.selected_packs
    pack = installed_pack("gli-19-readiness")[1]
    assert (selection.pack_id, selection.edition, selection.revision) == (
        pack.id,
        pack.edition,
        pack.revision,
    )
    assert (selection.artifact_ref, selection.sha256) == (None, None)
    assert selection.profile == framework["systemInputs"]
    assert selection.evidence_refs == ["art_saved_record"]
    assert selection.previous_assessment_ref == "art_previous"
    assert selection.requirement_choices["GLI-GSF-2:B"].systems == ["casino"]
    # Normalizing an already normalized request is a no-op.
    again = AssessmentRequest.model_validate(
        request.model_dump(mode="json", by_alias=True, exclude_none=True)
    )
    assert again.selected_packs == request.selected_packs

    nj = AssessmentRequest.model_validate(
        legacy_request("nj-dge-readiness", dict(framework, auditRef="art_annual"))
    )
    assert nj.selected_packs[0].audit_ref == "art_annual"
    # Other built-ins keep their own path; invalid saved input stays an error.
    iso = AssessmentRequest.model_validate(
        legacy_request("iso-27001-readiness", {"systemInputs": {}, "period": framework["period"]})
    )
    assert iso.framework.pack_id == "iso-27001-readiness" and not iso.selected_packs
    with pytest.raises(ValueError, match="framework"):
        AssessmentRequest.model_validate(
            legacy_request("gli-19-readiness", {"systemInputs": {}})
        )


def test_saved_built_in_joins_other_selected_packs():
    from moonmind.security.assessments import AssessmentRequest

    request = legacy_request(
        "gli-19-readiness",
        {"systemInputs": {}, "period": {"start": "2026-11-04", "end": "2026-11-07"}},
    )
    request["frameworkPack"] = {
        "artifactRef": "art_pack",
        "packId": "example",
        "edition": "2026",
        "revision": "1",
        "sha256": "0" * 64,
        "period": {"start": "2026-11-04", "end": "2026-11-07"},
    }
    parsed = AssessmentRequest.model_validate(request)
    assert [s.pack_id for s in parsed.selected_packs] == ["gli-19-readiness", "example"]
    request.pop("framework")
    request["frameworkPack"].pop("sha256")
    with pytest.raises(ValueError, match="artifactRef and sha256"):
        AssessmentRequest.model_validate(request)
