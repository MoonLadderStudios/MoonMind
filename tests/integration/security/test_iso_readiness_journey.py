"""Synthetic ISMS through catalog, real Activity, immutable artifacts and reports."""

from datetime import UTC, datetime

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
    assert first["frameworkRef"] == direct["frameworkRef"]
    assert first["frameworkReadiness"] == "incomplete"
    report = await recurring.read(service, first["frameworkRef"])
    assert report["statementOfApplicability"]["status"] == "draft"
    assert {"9.2", "9.3"} <= set(report["unresolvedRequirements"])
    assert first["publicationOutcome"] == "published"
    assert (
        first["reportBundle"]["structured_ref"]["artifact_id"] == first["frameworkRef"]
    )
    _, markdown = await service.read(
        artifact_id=recurring.report_ref(first),
        access=access_for_test("workflow:assessment"),
    )
    assert b"Draft Statement of Applicability" in markdown
    assert report["statementOfApplicability"]["version"].encode() in markdown
    again = await recurring.invoke(service, request)
    assert again["frameworkRef"] == first["frameworkRef"]


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
    framework = await recurring.read(service, result["frameworkRef"])
    _, body = await service.read(
        artifact_id=recurring.report_ref(result),
        access=access_for_test("workflow:assessment"),
    )
    report = body.decode()
    assert comparison["coverageStatus"] == "complete"
    assert result["methodologyStatus"] == "incomplete"
    assert result["reportBundle"]["report_scope"] == "partial"
    assert report.splitlines()[2] == "Coverage: partial. Baseline: initial."
    assert comparison["findings"][0]["findingId"] in report
    assert comparison["observation"]["observedAt"] in report
    assert framework["statementOfApplicability"]["version"] in report
    assert {"9.2", "9.3"} <= set(framework["unresolvedRequirements"])
    assert framework["certification"] == "Unverified"
    retry = await recurring.invoke(service, request)
    for ref in ("scanRef", "comparisonRef", "frameworkRef", "methodologyRef"):
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
    original = await recurring.read(service, first["frameworkRef"])
    assert current in await recurring.required(service, first["frameworkRef"])
    assert old not in await recurring.required(service, first["frameworkRef"])
    request["framework"]["previousAssessmentRef"] = first["frameworkRef"]
    request["framework"]["systemInputs"][
        "scopeStatement"
    ] = "Expanded organisational boundaries"
    changed = await recurring.invoke(service, request, key="changed")
    revised = await recurring.read(service, changed["frameworkRef"])
    assert (
        revised["reassessment"]["previousSoAVersion"]
        == original["statementOfApplicability"]["version"]
    )
    assert revised["reassessment"]["affectedControlIds"]
    assert await recurring.read(service, first["frameworkRef"]) == original
    assert first["frameworkRef"] in await recurring.required(
        service, changed["frameworkRef"]
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
    report = await recurring.read(service, fixed["frameworkRef"])
    assert {"9.2", "9.3", "8.3"} <= set(report["unresolvedRequirements"])
    assert fixed["frameworkReadiness"] == "incomplete"
    assert report["certification"] == "Unverified"


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
    report = await recurring.read(service, result["frameworkRef"])
    row = next(r for r in report["requirements"] if r["id"] == "9.2")
    assert row["status"] == "incomplete"
    assert row["evidenceReviews"][0]["state"] == "insufficient"
    assert "unreadable" in " ".join(row["evidenceReviews"][0]["limitations"])
    assert current not in await recurring.required(service, result["frameworkRef"])


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
    report = await recurring.read(service, result["frameworkRef"])
    rows = {r["id"]: r for r in report["requirements"]}
    assert (
        rows["A.8.8"]["technicalContextRefs"]
        == rows["9.3"]["technicalContextRefs"]
        == [mapping]
    )
    assert rows["A.8.8"]["status"] == rows["9.3"]["status"] == "incomplete"
    assert {mapping, scan["scanRef"]} <= await recurring.required(
        service, result["frameworkRef"]
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
    original = await recurring.read(service, first["frameworkRef"])
    request["framework"]["previousAssessmentRef"] = first["frameworkRef"]
    request["framework"]["requirementChoices"] = {"9.2": {"systems": ["isms"]}}
    revised = await recurring.invoke(service, request, key="revised-audit-scope")
    report = await recurring.read(service, revised["frameworkRef"])
    assert report["reassessment"]["affectedRequirementIds"] == ["9.2"]
    assert report["reassessment"]["affectedControlIds"] == []
    prompt = next(
        r
        for r in revised["evidenceRequests"]
        if r["id"] == "reassessment.applicability"
    )
    assert prompt["affectedRequirementIds"] == ["9.2"]
    assert (
        report["statementOfApplicability"]["version"]
        == original["statementOfApplicability"]["version"]
    )
    assert await recurring.read(service, first["frameworkRef"]) == original
    assert first["frameworkRef"] in await recurring.required(
        service, revised["frameworkRef"]
    )
