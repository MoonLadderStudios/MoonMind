"""Synthetic ISMS through catalog, real Activity, immutable artifacts and reports.

ISO/IEC 27001 runs as an installed pack on the shared control evaluator; the
saved ``framework.packId`` shape these journeys submit normalizes once at the
request boundary.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from api_service.services.presets.catalog import PresetCatalogService
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring
from tests.unit.api.test_iso_security_preset import seed
from tests.unit.api.test_presets_service import template_db
from tests.unit.security.test_iso_27001 import reviewed_evidence, selection

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment


def payload():
    return {
        "scope": {"assets": ["isms"]},
        "tools": [],
        "budget": {"baseSeconds": 60, "maxSeconds": 120, "progressStallSeconds": 60},
        "credentialRefs": [],
        "networkRefs": [],
        "framework": selection().model_dump(mode="json", by_alias=True),
    }


async def readiness(service, outputs):
    """The retained control report and its ISO readiness context."""
    report = await recurring.read(service, outputs["controlAssessmentRef"])
    return report, report["frameworkPack"]["readiness"]


def statuses(report):
    return {
        row["requirement"]["id"]: row["requirement"]["readiness"]["status"]
        for row in report["requirements"]
    }


async def test_catalog_direct_and_retry_keep_same_soa_and_export(environment, tmp_path):
    service = environment.service
    request = payload()
    async with template_db(tmp_path) as maker, maker() as session:
        await seed(session, tmp_path)
        expanded = await PresetCatalogService(session).expand_template(
            slug="security-iso-27001",
            inputs={
                "assets": ["isms"],
                "system_inputs": request["framework"]["systemInputs"],
                "period": request["framework"]["period"],
            },
        )
    first = await recurring.invoke(service, expanded["steps"][0]["tool"]["inputs"])
    direct = await recurring.invoke(service, request, key="direct")
    assert first["controlAssessmentRef"] == direct["controlAssessmentRef"]
    assert first["frameworkPack"]["readiness"] == "incomplete"
    report, context = await readiness(service, first)
    assert report["frameworkPack"]["id"] == "iso-27001-readiness"
    assert context["statementOfApplicability"]["status"] == "draft"
    assert {"9.2", "9.3"} <= set(context["unresolvedRequirements"])
    assert first["publicationOutcome"] == "published"
    assert (
        first["reportBundle"]["structured_ref"]["artifact_id"]
        == first["controlAssessmentRef"]
    )
    _, markdown = await service.read(
        artifact_id=recurring.report_ref(first),
        access=access_for_test("workflow:assessment"),
    )
    assert b"Draft Statement of Applicability version" in markdown
    assert context["statementOfApplicability"]["version"].encode() in markdown
    again = await recurring.invoke(service, request)
    assert again["controlAssessmentRef"] == first["controlAssessmentRef"]


async def test_iso_report_preserves_methodology_gaps_and_native_findings(environment):
    from tests.integration.security.test_web_api_methodology_journey import retain_plan

    service = environment.service
    request, _, _ = await recurring.inputs(service)
    request["framework"] = selection(systems=["app"]).model_dump(
        mode="json", by_alias=True
    )
    plan_ref, _ = await retain_plan(service)
    request["methodology"] = {"planRef": plan_ref}

    result = await recurring.invoke(service, request)
    comparison = await recurring.read(service, result["comparisonRef"])
    _, context = await readiness(service, result)
    _, body = await service.read(
        artifact_id=recurring.report_ref(result),
        access=access_for_test("workflow:assessment"),
    )
    report = body.decode()
    assert comparison["coverageStatus"] == "complete"
    assert result["methodologyStatus"] == "incomplete"
    assert result["reportBundle"]["report_scope"] == "partial"
    assert "Coverage: partial. Baseline: initial." in report.splitlines()
    assert comparison["findings"][0]["findingId"] in report
    assert comparison["observation"]["observedAt"] in report
    assert context["statementOfApplicability"]["version"] in report
    assert {"9.2", "9.3"} <= set(context["unresolvedRequirements"])
    assert context["externalClaims"]["certification"]["status"] == "Unverified"
    retry = await recurring.invoke(service, request)
    for ref in ("scanRef", "comparisonRef", "controlAssessmentRef", "methodologyRef"):
        assert retry[ref] == result[ref]


async def test_revised_scope_preserves_historical_soa_and_current_evidence(environment):
    service = environment.service
    request = payload()
    evidence = reviewed_evidence()
    old = await recurring.put(service, {"record": "superseded audit"})
    current = await recurring.put(service, {"record": "completed audit"})
    evidence["revisions"][0]["artifactRef"] = old
    evidence["revisions"].append(dict(evidence["revisions"][0], artifactRef=current))
    request["framework"]["systemInputs"]["requirementEvidence"] = {"9.2": [evidence]}
    first = await recurring.invoke(service, request)
    original, prior = await readiness(service, first)
    assert current in await recurring.required(service, first["controlAssessmentRef"])
    assert old not in await recurring.required(service, first["controlAssessmentRef"])
    request["framework"]["previousAssessmentRef"] = first["controlAssessmentRef"]
    request["framework"]["systemInputs"][
        "scopeStatement"
    ] = "Expanded organisational boundaries"
    changed = await recurring.invoke(service, request, key="changed")
    _, revised = await readiness(service, changed)
    assert (
        revised["reassessment"]["previousSoAVersion"]
        == prior["statementOfApplicability"]["version"]
    )
    assert revised["reassessment"]["affectedControlIds"]
    assert await recurring.read(service, first["controlAssessmentRef"]) == original
    assert first["controlAssessmentRef"] in await recurring.required(
        service, changed["controlAssessmentRef"]
    )


async def test_fixed_vulnerability_retest_cannot_close_isms_process_gaps(environment):
    service = environment.service
    request, inventory, _ = await recurring.inputs(service)
    framework = selection().model_dump(mode="json", by_alias=True)
    framework["systemInputs"]["systems"] = ["app"]
    day = datetime.now(UTC).date().isoformat()
    framework["period"] = {"start": day, "end": day}
    request["framework"] = framework
    first = await recurring.invoke(service, request)
    inventory["components"][0]["version"] = "2.0"
    request["inventoryRef"] = await recurring.put(service, inventory)
    request["baselineRef"] = first["comparisonRef"]
    finding = (await recurring.read(service, first["comparisonRef"]))["findings"][0][
        "findingId"
    ]
    request["decisionsRef"] = await recurring.put(
        service, [recurring.decision(finding, [request["inventoryRef"]])]
    )
    fixed = await recurring.invoke(service, request, key="fixed")
    comparison = await recurring.read(service, fixed["comparisonRef"])
    assert comparison["findings"][0]["state"] == "remediated_verified"
    _, context = await readiness(service, fixed)
    assert {"9.2", "9.3", "8.3"} <= set(context["unresolvedRequirements"])
    assert fixed["frameworkPack"]["readiness"] == "incomplete"
    assert context["externalClaims"]["certification"]["status"] == "Unverified"


async def test_missing_current_document_stays_gap_without_reading_superseded(
    environment, monkeypatch
):
    from moonmind.workflows.temporal.artifacts import TemporalArtifactAuthorizationError

    service = environment.service
    request = payload()
    item = reviewed_evidence()
    old = await recurring.put(service, {"record": "history"})
    current = await recurring.put(service, {"record": "current"})
    item["revisions"][0]["artifactRef"] = old
    item["revisions"].append(dict(item["revisions"][0], artifactRef=current))
    request["framework"]["systemInputs"]["requirementEvidence"] = {"9.2": [item]}
    read = service.read

    async def unavailable(*args, **kwargs):
        if kwargs.get("artifact_id") == old:
            raise AssertionError("Superseded evidence must not be read")
        if kwargs.get("artifact_id") == current:
            raise TemporalArtifactAuthorizationError("Synthetic revoked document")
        return await read(*args, **kwargs)

    monkeypatch.setattr(service, "read", unavailable)
    result = await recurring.invoke(service, request)
    report, context = await readiness(service, result)
    assert statuses(report)["9.2"] == "incomplete"
    [review] = context["requirementDetails"]["9.2"]["evidenceReviews"]
    assert review["state"] == "insufficient"
    assert "unreadable" in " ".join(review["limitations"])
    assert current not in await recurring.required(
        service, result["controlAssessmentRef"]
    )


async def test_real_technical_evidence_is_reused_and_sealed_per_requirement(
    environment,
):
    service = environment.service
    request, _, _ = await recurring.inputs(service, rules=False)
    scan = await recurring.invoke(service, request)
    request["framework"] = selection().model_dump(mode="json", by_alias=True)
    request["framework"]["systemInputs"]["systems"] = ["app"]
    period = request["framework"]["period"]
    record = {
        "kind": "technical_assessment",
        "requirementIds": ["A.8.8", "9.3"],
        "systems": ["app"],
        "period": period,
        "method": "evidence",
        "description": "Retained synthetic vulnerability result; context only",
        "provenance": {
            "sourceRef": scan["scanRef"],
            "issuer": "Synthetic scanner",
            "version": "1",
            "recordedOn": datetime.now(UTC).date().isoformat(),
        },
        "artifactRef": "art_placeholder",
    }
    mapping = await recurring.put(service, record)
    request["framework"]["evidenceRefs"] = [mapping]
    result = await recurring.invoke(service, request, key="mapped")
    report, context = await readiness(service, result)
    details = context["requirementDetails"]
    assert (
        details["A.8.8"]["technicalContextRefs"]
        == details["9.3"]["technicalContextRefs"]
        == [mapping]
    )
    # Technical context never replaces the organisational record.
    assert statuses(report)["A.8.8"] == statuses(report)["9.3"] == "incomplete"
    assert {mapping, scan["scanRef"]} <= await recurring.required(
        service, result["controlAssessmentRef"]
    )


async def test_selected_supporting_document_export_withholds_superseded_revision(
    environment,
):
    from tests.unit.security.test_assessment_evidence_exports import unpack

    service = environment.service
    request = payload()
    old = await recurring.put(service, {"record": "history"})
    artifact, _ = await service.create(
        principal="operator",
        content_type="text/plain",
        link={
            "namespace": "default",
            "workflow_id": "assessment",
            "run_id": "run",
            "link_type": "input.security",
        },
    )
    current = artifact.artifact_id
    await service.write_complete(
        artifact_id=current,
        principal="operator",
        payload=b"Synthetic completed audit record",
    )
    item = reviewed_evidence()
    item["revisions"][0]["artifactRef"] = old
    item["revisions"].append(dict(item["revisions"][0], artifactRef=current))
    request["framework"]["systemInputs"]["requirementEvidence"] = {"9.2": [item]}
    request["evidenceExport"] = {"artifactRefs": [current, old]}
    result = await recurring.invoke(service, request)
    archive, manifest = await unpack(service, result)
    entries = {row["sourceArtifactId"]: row for row in manifest["entries"]}
    assert entries[current]["status"] == "included"
    assert (
        archive.read(entries[current]["memberName"])
        == b"Synthetic completed audit record"
    )
    assert entries[old]["status"] == "withheld"
    assert entries[old]["reason"] == "unassociated_evidence"


async def test_management_scope_revision_flags_only_affected_requirement_and_preserves_report(
    environment,
):
    service = environment.service
    request = payload()
    request["scope"]["assets"] = ["isms", "second"]
    request["framework"]["systemInputs"]["systems"] = ["isms", "second"]
    first = await recurring.invoke(service, request)
    original, prior = await readiness(service, first)
    request["framework"]["previousAssessmentRef"] = first["controlAssessmentRef"]
    request["framework"]["requirementChoices"] = {"9.2": {"systems": ["isms"]}}
    revised = await recurring.invoke(service, request, key="revised-audit-scope")
    _, context = await readiness(service, revised)
    assert context["reassessment"]["affectedRequirementIds"] == ["9.2"]
    assert context["reassessment"]["affectedControlIds"] == []
    prompt = next(
        r
        for r in context["evidenceRequests"]
        if r["id"] == "reassessment.applicability"
    )
    assert prompt["affectedRequirementIds"] == ["9.2"]
    assert (
        context["statementOfApplicability"]["version"]
        == prior["statementOfApplicability"]["version"]
    )
    assert await recurring.read(service, first["controlAssessmentRef"]) == original
    assert first["controlAssessmentRef"] in await recurring.required(
        service, revised["controlAssessmentRef"]
    )


async def test_retained_legacy_iso_report_renders_exports_and_is_a_previous_assessment(
    environment,
):
    """A retained iso-27001-readiness/v1 report stays usable after #5436."""
    from moonmind.workflows.skills.security_assessment import _record
    from tests.unit.security.test_assessment_evidence_exports import unpack

    service = environment.service
    retained = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "fixtures/security/legacy_readiness_reports.json"
        ).read_text()
    )["reports"]["iso-27001-readiness/v1"]
    previous = await recurring.put(service, retained)
    await _record(service, previous, [], access_for_test("workflow:assessment"))
    request = payload()
    request["framework"]["previousAssessmentRef"] = previous
    request["evidenceExport"] = {"artifactRefs": [previous]}
    result = await recurring.invoke(service, request)
    report, context = await readiness(service, result)
    pack = report["frameworkPack"]
    assert pack["previousAssessmentStatus"] == "available"
    assert pack["previousReportSummary"].startswith(
        "Previous iso-27001-readiness/v1 report"
    )
    # The legacy report carries no pack digests, so every decision is reviewed.
    assert context["reassessment"]["previousSoAVersion"] == (
        retained["statementOfApplicability"]["version"]
    )
    assert set(context["reassessment"]["affectedControlIds"]) >= {"A.8.8", "EXTRA.1"}
    _, markdown = await service.read(
        artifact_id=recurring.report_ref(result),
        access=access_for_test("workflow:assessment"),
    )
    assert b"Previous iso-27001-readiness/v1 report" in markdown
    archive, manifest = await unpack(service, result)
    entries = {row["sourceArtifactId"]: row for row in manifest["entries"]}
    assert entries[previous]["status"] == "included"
    assert json.loads(archive.read(entries[previous]["memberName"]))["pack"]["id"] == (
        "iso-27001-readiness"
    )
    assert previous in await recurring.required(service, result["controlAssessmentRef"])
