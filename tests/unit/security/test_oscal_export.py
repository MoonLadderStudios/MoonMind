"""OSCAL subset schema and semantics for MoonLadderStudios/MoonMind#5192."""

from __future__ import annotations

import copy
import json
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import jsonschema
import pytest

from moonmind.security.assessments import (
    AssessmentScope,
    Comparison,
    Coverage,
    Finding,
    Observation,
    compare_observations,
)
from moonmind.security.frameworks import FrameworkAssessmentInput
from moonmind.security.ukgc_rts import UKGC_RTS_PACK, prepare_audit

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/security"
MODELS = ["catalog", "profile", "assessment-plan", "assessment-results", "poam"]


def uid(value):
    return str(uuid5(NAMESPACE_URL, value))


def native_fixture():
    data = json.loads((FIXTURES / "ukgc_rts.json").read_text())
    selection = FrameworkAssessmentInput.model_validate(data["framework"])
    report = prepare_audit(
        selection,
        evidence=[
            dict(e, artifactRef=f"art_evidence_{i}")
            for i, e in enumerate(data["evidence"])
        ],
        audit=data["audit"],
    )
    now = datetime(2026, 11, 7, 12, tzinfo=UTC)
    scope = AssessmentScope(assets=["casino"])
    finding = Finding(
        source="fixture",
        tool="retained-report",
        rule_id="test-rule",
        asset="casino",
        native_id="native-1",
        evidence_refs=["art_scan_evidence"],
    )
    observation = Observation(
        scope=scope,
        observed_at=now,
        expires_at=now + timedelta(days=1),
        coverage=[
            Coverage(
                source="fixture",
                tool="retained-report",
                assets=["casino"],
                rule_ids=["test-rule"],
                config_ref="config:fixture",
                status="partial",
            )
        ],
        findings=[finding],
        provenance={"source": "art_scan_evidence"},
    )
    comparison = Comparison.model_validate(
        compare_observations(observation, None, now=now)
    )
    context = {
        "schemaVersion": "oscal-export-context/v1",
        "system": {"identifier": "urn:system:casino", "sspRef": "art_ssp"},
        "subjects": {"casino": uid("casino-component")},
        "parties": [
            {"uuid": uid("assessor"), "name": "Fixture assessor", "type": "person"}
        ],
        "procedures": [
            {
                "id": "access-review",
                "controlId": "5.15",
                "objective": "Assess the recorded access review.",
                "methods": ["EXAMINE", "INTERVIEW"],
                "steps": ["Inspect the scoped review record."],
                "assets": ["casino"],
                "responsibleParty": uid("assessor"),
            }
        ],
        "findingTargets": {
            finding.finding_id: {
                "procedureId": "access-review",
                "status": "not-satisfied",
                "reason": "The supplied access review records a missing revocation.",
            }
        },
        "remediations": [
            {
                "id": "remediation-1",
                "findingId": finding.finding_id,
                "riskStatement": "Unrevoked access permits unintended use.",
                "status": "remediating",
                "description": "Revoke and review access.",
                "responsibleParty": uid("assessor"),
                "evidenceRefs": ["art_action_record"],
                "actions": [
                    {
                        "id": "revoke",
                        "description": "Revoke the identified account and retest.",
                        "lifecycle": "planned",
                    }
                ],
                "milestones": [
                    {
                        "id": "retest",
                        "description": "Verify access is removed.",
                        "due": "2026-11-10T12:00:00Z",
                    }
                ],
            }
        ],
    }
    ssp = {
        "system-security-plan": {
            "uuid": uid("ssp"),
            "system-characteristics": {"system-ids": [{"id": "urn:system:casino"}]},
            "system-implementation": {
                "components": [{"uuid": uid("casino-component"), "title": "Casino"}]
            },
            "control-implementation": {
                "implemented-requirements": [
                    {"control-id": "control." + r.id}
                    for r in UKGC_RTS_PACK.requirements
                ]
            },
        }
    }
    return comparison, report, context, ssp


def documents(context_change=None):
    from moonmind.security.oscal_export import OscalAdapter, OscalContext

    comparison, report, context, ssp = native_fixture()
    if context_change:
        context_change(context)
    adapter = OscalAdapter(
        comparison=comparison,
        pack=UKGC_RTS_PACK,
        framework_report=report,
        context=OscalContext.model_validate(context),
        ssp=ssp,
    )
    refs = {model: f'art_{model.replace("-","_")}' for model in MODELS}
    return {model: adapter.build(model, refs) for model in MODELS}, (
        comparison,
        report,
        context,
        ssp,
    )


def validate_document(model, document):
    schema = json.loads(
        (FIXTURES / f"oscal/1.2.3/oscal_{model}_schema.json").read_text()
    )

    # NIST's name datatype uses Unicode categories, which Python re does not
    # implement. Enforce that exact expression; all other patterns retain the
    # standard validator. The official schema's JSON content stays unchanged.
    def pattern(validator, expression, instance, subschema):
        if expression == r"^(\p{L}|_)(\p{L}|\p{N}|[.\-_])*$":
            if isinstance(instance, str) and not (
                instance
                and (
                    instance[0] == "_"
                    or unicodedata.category(instance[0]).startswith("L")
                )
                and all(
                    ch in "._-" or unicodedata.category(ch)[0] in "LN"
                    for ch in instance
                )
            ):
                yield jsonschema.ValidationError(
                    "Invalid OSCAL Unicode name: " + instance
                )
        else:
            yield from jsonschema.Draft7Validator.VALIDATORS["pattern"](
                validator, expression, instance, subschema
            )

    validator = jsonschema.validators.extend(
        jsonschema.Draft7Validator, {"pattern": pattern}
    )
    validator(schema, format_checker=jsonschema.FormatChecker()).validate(document)


def test_opt_in_request_preserves_native_default():
    from moonmind.security.assessments import AssessmentRequest
    from tests.unit.security.test_ukgc_rts import request_payload

    payload = request_payload()
    payload["oscalExport"] = {"models": MODELS, "contextRef": "art_context"}
    assert (
        AssessmentRequest.model_validate(payload).oscal_export.context_ref
        == "art_context"
    )
    payload.pop("oscalExport")
    assert AssessmentRequest.model_validate(payload).oscal_export is None


@pytest.mark.parametrize("model", MODELS)
def test_complete_documents_validate_against_official_release(model):
    docs, _ = documents()
    validate_document(model, docs[model])


def test_supported_semantics_and_references():
    docs, (comparison, report, context, ssp) = documents()
    catalog = docs["catalog"]["catalog"]
    assert {c["id"] for c in catalog["controls"]} == {
        "control." + r.id for r in UKGC_RTS_PACK.requirements
    }
    row = next(c for c in catalog["controls"] if c["id"] == "control.5.15")
    props = {p["name"]: p["value"] for p in row["props"]}
    assert json.loads(props["native-assessment"]) == next(
        r for r in report["requirements"] if r["id"] == "5.15"
    )
    profile = docs["profile"]["profile"]
    assert profile["imports"][0]["href"] == "urn:moonmind:artifact:art_catalog"
    plan = docs["assessment-plan"]["assessment-plan"]
    assert plan["import-ssp"]["href"] == "urn:moonmind:artifact:art_ssp"
    assert (
        plan["assessment-subjects"][0]["include-subjects"][0]["subject-uuid"]
        == context["subjects"]["casino"]
    )
    assert plan["tasks"][0]["responsible-roles"][0]["party-uuids"] == [
        context["parties"][0]["uuid"]
    ]
    assert (
        plan["local-definitions"]["objectives-and-methods"][0]["control-id"]
        == "control.5.15"
    )
    assert (
        docs["assessment-results"]["assessment-results"]["import-ap"]["href"]
        == "urn:moonmind:artifact:art_assessment_plan"
    )
    result = docs["assessment-results"]["assessment-results"]["results"][0]
    native_observation = next(
        o
        for o in result["observations"]
        if any(p["name"] == "native-finding-id" for p in o.get("props", []))
    )
    props = {p["name"]: p["value"] for p in native_observation["props"]}
    assert props["native-finding-id"] == comparison.findings[0].finding_id
    assert props["native-state"] == comparison.findings[0].state
    assert {p["name"]: p["value"] for p in result["props"]}[
        "coverage-status"
    ] == "stale_or_partial"
    finding = result["findings"][0]
    assert finding["target"]["status"]["state"] == "not-satisfied"
    assert (
        finding["related-observations"][0]["observation-uuid"]
        == native_observation["uuid"]
    )
    poam = docs["poam"]["plan-of-action-and-milestones"]
    risk = poam["risks"][0]
    assert poam["poam-items"][0]["related-risks"] == [{"risk-uuid": risk["uuid"]}]
    assert risk["status"] == "remediating"
    assert (
        risk["remediations"][0]["tasks"][0]["description"]
        == context["remediations"][0]["actions"][0]["description"]
    )
    assert (
        risk["remediations"][0]["tasks"][1]["timing"]["on-date"]["date"]
        == "2026-11-10T12:00:00Z"
    )
    assert poam["system-id"]["id"] == "urn:system:casino"
    assert "external" in catalog["metadata"]["remarks"].lower()


@pytest.mark.parametrize("missing", ["system", "parties", "procedures"])
def test_target_scope_is_not_a_full_plan(missing):
    from moonmind.security.oscal_export import (
        OscalAdapter,
        OscalContext,
        UnsupportedExport,
    )

    comparison, report, context, ssp = native_fixture()
    context.pop(missing)
    adapter = OscalAdapter(
        comparison=comparison,
        pack=UKGC_RTS_PACK,
        framework_report=report,
        context=OscalContext.model_validate(context),
        ssp=ssp,
    )
    with pytest.raises(UnsupportedExport, match=missing):
        adapter.build("assessment-plan", {})


def test_comparison_is_not_a_remediation_plan():
    from moonmind.security.oscal_export import (
        OscalAdapter,
        OscalContext,
        UnsupportedExport,
    )

    comparison, report, context, ssp = native_fixture()
    context.pop("remediations")
    adapter = OscalAdapter(
        comparison=comparison,
        pack=UKGC_RTS_PACK,
        framework_report=report,
        context=OscalContext.model_validate(context),
        ssp=ssp,
    )
    with pytest.raises(UnsupportedExport, match="remediation"):
        adapter.build("poam", {})


@pytest.mark.parametrize(
    "fault",
    [
        "foreign-system",
        "foreign-subject",
        "foreign-control",
        "foreign-finding",
        "native-state",
        "unknown-relationship",
    ],
)
def test_unmappable_context_is_diagnostic(fault):
    from moonmind.security.oscal_export import (
        OscalAdapter,
        OscalContext,
        UnsupportedExport,
    )

    comparison, report, context, ssp = native_fixture()
    if fault == "foreign-system":
        context["system"]["identifier"] = "urn:other"
    elif fault == "foreign-subject":
        context["subjects"]["casino"] = uid("unknown")
    elif fault == "foreign-control":
        context["procedures"][0]["controlId"] = "nonexistent"
    elif fault == "foreign-finding":
        context["findingTargets"]["unknown"] = context["findingTargets"].pop(
            comparison.findings[0].finding_id
        )
    elif fault == "unknown-relationship":
        context["procedures"][0]["assets"] = ["outside"]
    else:
        comparison.findings[0].state = "invented-state"
    with pytest.raises(UnsupportedExport):
        adapter = OscalAdapter(
            comparison=comparison,
            pack=UKGC_RTS_PACK,
            framework_report=report,
            context=OscalContext.model_validate(context),
            ssp=ssp,
        )
        adapter.build(
            "assessment-results",
            {
                "assessment-plan": "art_plan",
                "catalog": "art_catalog",
                "profile": "art_profile",
            },
        )


def test_imported_context_cannot_request_authority():
    from pydantic import ValidationError

    from moonmind.security.oscal_export import OscalContext

    _, _, context, _ = native_fixture()
    context["credentialRefs"] = ["secret:outside"]
    with pytest.raises(ValidationError):
        OscalContext.model_validate(context)


@pytest.mark.parametrize("fault", ["control", "malformed-system", "uuid"])
def test_missing_or_malformed_ssp_context_is_unsupported(fault):
    from pydantic import ValidationError

    from moonmind.security.oscal_export import (
        OscalAdapter,
        OscalContext,
        UnsupportedExport,
    )

    comparison, report, context, ssp = native_fixture()
    if fault == "uuid":
        context["parties"][0]["uuid"] = "00000000-0000-0000-0000-000000000000"
        with pytest.raises(ValidationError):
            OscalContext.model_validate(context)
        return
    if fault == "control":
        ssp["system-security-plan"].pop("control-implementation", None)
    else:
        ssp["system-security-plan"]["system-characteristics"] = []
    with pytest.raises(UnsupportedExport):
        OscalAdapter(
            comparison=comparison,
            pack=UKGC_RTS_PACK,
            framework_report=report,
            context=OscalContext.model_validate(context),
            ssp=ssp,
        ).build("assessment-plan", {"profile": "art_profile"})


def test_unknown_native_framework_state_is_unsupported():
    from moonmind.security.oscal_export import OscalAdapter, UnsupportedExport

    comparison, report, _, _ = native_fixture()
    report["requirements"][0]["status"] = "invented-status"
    with pytest.raises(UnsupportedExport):
        OscalAdapter(
            comparison=comparison, pack=UKGC_RTS_PACK, framework_report=report
        ).build("catalog", {})


@pytest.mark.parametrize(
    "pack_id", ["ukgc-rts-security", "gli-19-readiness", "nj-dge-readiness"]
)
def test_existing_framework_owners_preserve_applicability(pack_id):
    from moonmind.security.frameworks import load_framework_pack
    from moonmind.security.oscal_export import OscalAdapter
    from tests.unit.security import test_gli_readiness, test_nj_dge, test_ukgc_rts

    source = {
        "ukgc-rts-security": test_ukgc_rts,
        "gli-19-readiness": test_gli_readiness,
        "nj-dge-readiness": test_nj_dge,
    }[pack_id]
    comparison, _, _, _ = native_fixture()
    report = source.evaluate()
    # A native non-applicable record stays in the catalog and out of selection.
    report["requirements"][0]["status"] = "not_applicable"
    adapter = OscalAdapter(
        comparison=comparison,
        pack=load_framework_pack(pack_id),
        framework_report=report,
    )
    catalog = adapter.build("catalog", {})
    profile = adapter.build("profile", {"catalog": "art_catalog"})
    validate_document("catalog", catalog)
    validate_document("profile", profile)
    excluded = catalog["catalog"]["controls"][0]["id"]
    assert (
        excluded
        not in profile["profile"]["imports"][0]["include-controls"][0]["with-ids"]
    )
    assert (
        json.loads(catalog["catalog"]["controls"][0]["props"][-1]["value"])["status"]
        == "not_applicable"
    )


def test_native_accepted_risk_and_deviations_remain_attributed():
    from moonmind.security.assessments import ComparedDecision, TriageDecision
    from moonmind.security.oscal_export import OscalAdapter, OscalContext

    comparison, report, context, ssp = native_fixture()
    decision = TriageDecision(
        finding_id=comparison.findings[0].finding_id,
        disposition="accepted_risk",
        reason="Recorded temporary risk acceptance.",
        evidence_refs=["art_acceptance"],
        author="Fixture operator",
        source="fixture",
        scope=comparison.observation.scope,
        review_at=comparison.observation.expires_at,
        review_conditions=["Review at milestone"],
    )
    comparison.decisions = [ComparedDecision(decision=decision, status="active")]
    context["remediations"][0]["status"] = "deviation-approved"
    adapter = OscalAdapter(
        comparison=comparison,
        pack=UKGC_RTS_PACK,
        framework_report=report,
        context=OscalContext.model_validate(context),
        ssp=ssp,
    )
    doc = adapter.build("poam", {"assessment-plan": "art_plan"})
    validate_document("poam", doc)
    body = doc["plan-of-action-and-milestones"]
    assert body["risks"][0]["status"] == "deviation-approved"
    preserved = json.loads(
        next(
            p["value"]
            for p in body["findings"][0]["props"]
            if p["name"] == "native-decisions"
        )
    )
    assert preserved == [comparison.decisions[0].model_dump(mode="json", by_alias=True)]


def test_technical_observations_do_not_invent_objective_findings():
    from moonmind.security.oscal_export import OscalAdapter, OscalContext

    comparison, report, context, ssp = native_fixture()
    context["findingTargets"] = {}
    context["remediations"] = []
    adapter = OscalAdapter(
        comparison=comparison,
        pack=UKGC_RTS_PACK,
        framework_report=report,
        context=OscalContext.model_validate(context),
        ssp=ssp,
    )
    doc = adapter.build("assessment-results", {"assessment-plan": "art_plan"})
    validate_document("assessment-results", doc)
    result = doc["assessment-results"]["results"][0]
    assert "findings" not in result
    assert any(
        any(p["name"] == "native-finding-id" for p in o.get("props", []))
        for o in result["observations"]
    )


def test_schema_validation_enforces_names_and_rejects_unknown_fields():
    doc, _ = documents()
    for bad in ["1control", "control:invalid", "invalid/name"]:
        changed = copy.deepcopy(doc["catalog"])
        changed["catalog"]["controls"][0]["id"] = bad
        with pytest.raises(jsonschema.ValidationError):
            validate_document("catalog", changed)
    changed = copy.deepcopy(doc["assessment-plan"])
    changed["assessment-plan"]["invented-field"] = True
    with pytest.raises(jsonschema.ValidationError):
        validate_document("assessment-plan", changed)
