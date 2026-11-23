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
from moonmind.security.framework_registry import installed_pack
from moonmind.security.frameworks import EvidencePeriod

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures/security"
MODELS = ["catalog", "profile", "assessment-plan", "assessment-results", "poam"]


def uid(value):
    return str(uuid5(NAMESPACE_URL, value))


UKGC_RTS_PACK = installed_pack("ukgc-rts-security")[1]


def ukgc_readiness(data=None):
    """The fixture's UKGC readiness on the shared pack evaluator."""
    from moonmind.security.framework_registry import pack_behavior
    from moonmind.security.frameworks import normalize_installed_framework
    from moonmind.security.pack_readiness import evaluate_readiness, parse_evidence

    data = data or json.loads((FIXTURES / "ukgc_rts.json").read_text())
    [selection] = normalize_installed_framework({"framework": data["framework"]})[
        "frameworkPacks"
    ]
    behavior = pack_behavior(UKGC_RTS_PACK)
    return data, evaluate_readiness(
        UKGC_RTS_PACK,
        behavior,
        profile=selection["profile"],
        period=EvidencePeriod.model_validate(selection["period"]),
        choices=selection["requirementChoices"],
        records=[
            parse_evidence(behavior, e, f"art_evidence_{i}")
            for i, e in enumerate(data["evidence"])
        ],
        audit=behavior.records_model.model_validate(data["audit"]),
        admitted_assets=["casino"],
    )


def unified_report(pack, result, data, comparison):
    """The per-requirement rows an assessment run hands the OSCAL adapter."""
    from moonmind.security.control_assessments import assess_controls
    from moonmind.security.framework_packs import (
        compile_framework_plan,
        readiness_metadata,
    )
    from moonmind.security.oscal_export import readiness_report

    period = EvidencePeriod.model_validate(data["framework"]["period"])
    systems = sorted({s for row in result.rows.values() for s in row["systems"]})
    plan, _ = compile_framework_plan(
        pack,
        scope=AssessmentScope(assets=systems or ["casino"]),
        period=period,
        choices=data["framework"].get("requirementChoices", {}),
        now=datetime(2026, 11, 23, tzinfo=UTC),
        readiness=result,
    )
    control = assess_controls(
        plan,
        comparison,
        scan_ref="art_scan",
        comparison_ref="art_comparison",
        coverage_windows=[],
        now=datetime(2026, 11, 23, tzinfo=UTC),
        evidence_availability={},
    )
    metadata = {
        "id": pack.id,
        "edition": pack.edition,
        "readiness": json.loads(
            json.dumps(readiness_metadata(pack, result), default=str)
        ),
    }
    return control, metadata, readiness_report(control, metadata)


def native_fixture():
    data, result = ukgc_readiness()
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
    report = unified_report(UKGC_RTS_PACK, result, data, comparison)[2]
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

    payload = {
        "scope": {"assets": ["casino"]},
        "tools": [],
        "budget": {"baseSeconds": 30, "maxSeconds": 60, "progressStallSeconds": 30},
        "credentialRefs": [],
        "networkRefs": [],
        "framework": json.loads((FIXTURES / "ukgc_rts.json").read_text())["framework"],
    }
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


@pytest.mark.parametrize("line_break", ["\n", "\r\n"])
def test_multiline_objectives_preserve_prose_in_valid_exports(line_break):
    objective = (
        "Assess the recorded access review."
        + line_break
        + "Confirm the recorded revocations."
    )

    def change(context):
        context["procedures"][0]["objective"] = objective

    docs, _ = documents(change)
    for model in MODELS:
        validate_document(model, docs[model])
    plan = docs["assessment-plan"]["assessment-plan"]
    assert plan["tasks"][0]["title"] == (
        "Assess the recorded access review. Confirm the recorded revocations."
    )
    definitions = plan["local-definitions"]
    assert definitions["objectives-and-methods"][0]["parts"][0]["prose"] == objective
    assert definitions["activities"][0]["description"] == objective


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


def test_existing_framework_owner_preserves_applicability():
    from moonmind.security.oscal_export import OscalAdapter

    comparison, report, _, _ = native_fixture()
    # A native non-applicable record stays in the catalog and out of selection.
    report["requirements"][0]["status"] = "not_applicable"
    adapter = OscalAdapter(
        comparison=comparison,
        pack=UKGC_RTS_PACK,
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


def unified_gaming_report(pack_id, comparison):
    """A unified control report for one installed gaming pack, as a run writes it."""
    from moonmind.security.control_assessments import assess_controls
    from moonmind.security.framework_packs import (
        compile_framework_plan,
        readiness_metadata,
    )
    from moonmind.security.framework_registry import installed_pack
    from tests.unit.security import test_gli_readiness, test_nj_dge

    pack = installed_pack(pack_id)[1]
    if pack_id == "gli-19-readiness":
        data = test_gli_readiness.fixture()
        result = test_gli_readiness.evaluate(data, evidence=[data["technicalEvidence"]])
    else:
        data = test_nj_dge.fixture()
        data["framework"]["requirementChoices"] = {
            "nj.partner.player-accounts": {
                "notApplicableReason": "Partner owns accounts"
            }
        }
        from moonmind.security.framework_registry import pack_behavior
        from moonmind.security.pack_readiness import evaluate_readiness, parse_evidence

        behavior = pack_behavior(pack)
        result = evaluate_readiness(
            pack,
            behavior,
            profile=data["framework"]["systemInputs"],
            period=EvidencePeriod.model_validate(data["framework"]["period"]),
            choices=data["framework"]["requirementChoices"],
            records=[
                parse_evidence(behavior, row, f"art_evidence_{i}")
                for i, row in enumerate(data["evidence"])
            ],
            audit=behavior.records_model.model_validate(data["audit"]),
            admitted_assets=["rgs"],
        )
    scope = AssessmentScope(assets=data["framework"]["systemInputs"]["systems"])
    plan, _ = compile_framework_plan(
        pack,
        scope=scope,
        period=EvidencePeriod.model_validate(data["framework"]["period"]),
        choices=data["framework"]["requirementChoices"],
        now=datetime(2026, 11, 23, tzinfo=UTC),
        readiness=result,
    )
    control = assess_controls(
        plan,
        comparison,
        scan_ref="art_scan",
        comparison_ref="art_comparison",
        coverage_windows=[],
        now=datetime(2026, 11, 23, tzinfo=UTC),
        evidence_availability={},
    )
    metadata = {
        "id": pack.id,
        "edition": pack.edition,
        "readiness": readiness_metadata(pack, result),
    }
    return pack, control, metadata


@pytest.mark.parametrize("pack_id", ["gli-19-readiness", "nj-dge-readiness"])
def test_unified_gaming_exports_validate_with_unchanged_control_identities(pack_id):
    from moonmind.security.oscal_export import (
        OscalAdapter,
        control_id,
        readiness_report,
    )

    golden = json.loads((FIXTURES / "gaming_equivalence.json").read_text())
    family = "gli" if pack_id.startswith("gli") else "nj"
    legacy_ids = [
        key
        for scenario in golden["scenarios"]
        if scenario["family"] == family
        for status in scenario["expected"]["statuses"].values()
        for key in status
    ]
    comparison, _, _, _ = native_fixture()
    pack, control, metadata = unified_gaming_report(pack_id, comparison)
    adapter = OscalAdapter(
        comparison=comparison,
        pack=pack,
        framework_report=readiness_report(control, metadata),
    )
    catalog = adapter.build("catalog", {})
    profile = adapter.build("profile", {"catalog": "art_catalog"})
    validate_document("catalog", catalog)
    validate_document("profile", profile)
    controls = [row["id"] for row in catalog["catalog"]["controls"]]
    assert set(controls) == {control_id(key) for key in legacy_ids}
    assert controls == [control_id(row.id) for row in pack.requirements]
    selected = profile["profile"]["imports"][0]["include-controls"][0]["with-ids"]
    statuses = {
        row.requirement.id: row.requirement.readiness.status
        for row in control.requirements
    }
    for key, status in statuses.items():
        assert (control_id(key) in selected) == (
            status
            not in {
                "not_applicable",
                "not_selected",
                "excluded",
                "exclusion_for_review",
            }
        )
    if family == "gli":
        # Same pack ID and edition, so the exchange identities are unchanged too.
        assert catalog["catalog"]["uuid"] == uid("gli-19-readiness:4.0::catalog")
        assert control_id("GLI-GSF-1:Appendix A") in controls
    else:
        assert control_id("nj.partner.player-accounts") not in selected


@pytest.mark.asyncio
async def test_restricted_declared_pack_is_not_copied_into_exports():
    from types import SimpleNamespace

    from moonmind.security.framework_registry import installed_pack
    from moonmind.workflows.skills.security_oscal import export_assessment

    comparison, _, _, _ = native_fixture()
    _, control, metadata = unified_gaming_report("gli-19-readiness", comparison)
    restricted = json.loads(installed_pack("gli-19-readiness")[0])
    restricted["distribution"] = "restricted"

    class Execution:
        async def read(self, ref):
            assert ref == "art_pack"
            return None, json.dumps(restricted).encode()

    result = await export_assessment(
        Execution(),
        request=SimpleNamespace(
            selected_packs=[restricted["id"]],
            oscal_export=SimpleNamespace(models=["catalog"], context_ref=None),
        ),
        comparison=comparison,
        comparison_ref="art_comparison",
        controls=(
            "art_control",
            SimpleNamespace(pack_metadata=[{**metadata, "artifactRef": "art_pack"}]),
        ),
    )
    assert result["status"] == "unsupported"
    assert result["artifacts"] == {}
    assert [d["code"] for d in result["diagnostics"]] == ["restricted_distribution"]


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


@pytest.mark.parametrize(
    "scenario_id",
    ["iso-responsibility-A.8.8-base", "soc2-complete", "ukgc-exclusion-and-narrowed"],
)
def test_migrated_built_in_exports_validate_with_unchanged_control_identities(
    scenario_id,
):
    """ISO/IEC 27001, SOC 2 and UKGC RTS export all five models from the unified
    report, with the control IDs the retired per-pack owners exported."""
    from moonmind.security.oscal_export import OscalAdapter, OscalContext, control_id
    from tests.unit.security.test_legacy_readiness_equivalence import (
        GOLDEN,
        SCENARIOS,
        evaluated,
        selection,
    )

    scenario = SCENARIOS[scenario_id]
    pack = installed_pack(selection(scenario)["packId"])[1]
    comparison, _, context, ssp = native_fixture()
    data = {"framework": scenario["framework"]}
    _, _, report = unified_report(pack, evaluated(scenario), data, comparison)
    # The plan reviews one included control of this pack on the casino system.
    context["procedures"][0]["controlId"] = {
        "iso-27001-readiness": "A.8.8",
        "soc2-readiness": "CC6.1",
        "ukgc-rts-security": "5.15",
    }[pack.id]
    ssp["system-security-plan"]["control-implementation"][
        "implemented-requirements"
    ] = [{"control-id": control_id(row.id)} for row in pack.requirements]
    adapter = OscalAdapter(
        comparison=comparison,
        pack=pack,
        framework_report=report,
        context=OscalContext.model_validate(context),
        ssp=ssp,
    )
    refs = {model: f'art_{model.replace("-", "_")}' for model in MODELS}
    built = {model: adapter.build(model, refs) for model in MODELS}
    for model, document in built.items():
        validate_document(model, document)
    controls = [row["id"] for row in built["catalog"]["catalog"]["controls"]]
    assert controls == [
        row[3] for row in GOLDEN["legacyPacks"][pack.id]["requirements"]
    ]
    selected = built["profile"]["profile"]["imports"][0]["include-controls"][0][
        "with-ids"
    ]
    for row in report["requirements"]:
        assert (control_id(row["id"]) in selected) == (
            row["status"]
            not in {
                "not_applicable",
                "not_selected",
                "excluded",
                "exclusion_for_review",
            }
        )
    if pack.id == "iso-27001-readiness":
        # Same pack ID and edition, so the exchange identities are unchanged too.
        assert built["catalog"]["catalog"]["uuid"] == uid(
            "iso-27001-readiness:2022+Amd1:2024:urn:system:casino:catalog"
        )
