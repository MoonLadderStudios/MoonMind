"""Synthetic preset -> native assessment -> retained report journey (#5197).

UKGC RTS runs as an installed pack on the shared control evaluator (#5436).
"""

import json
from unittest.mock import patch

import pytest

from api_service.services.presets.catalog import PresetCatalogService
from tests.artifact_access import access_for_test
from tests.integration.security import test_recurring_assessments as recurring
from tests.integration.security.test_recurring_assessments import inputs, invoke, put
from tests.unit.api.test_presets_service import template_db
from tests.unit.api.test_ukgc_security_preset import seed
from tests.unit.security.test_ukgc_rts import fixture, request_payload

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
environment = recurring.environment


async def retained(service, outputs):
    """The retained control report and its UKGC readiness context."""
    report = await recurring.read(service, outputs["controlAssessmentRef"])
    return report, report["frameworkPack"]["readiness"]


def statuses(report):
    return {
        row["requirement"]["id"]: row["requirement"]["readiness"]["status"]
        for row in report["requirements"]
    }


async def test_synthetic_audit_through_catalog_and_native_artifact_journey(
    environment, tmp_path
):
    service = environment.service
    data = fixture()
    record_refs = [await put(service, row) for row in data["evidence"]]
    # Independence and prior-review statements are evidence, never inferred
    # from the auditor's display name or a certification field.
    independence = await put(service, {"statement": "Synthetic independence statement"})
    data["audit"]["auditor"]["independenceRef"] = independence
    audit_ref = await put(service, data["audit"])
    async with template_db(tmp_path) as maker, maker() as session:
        await seed(session, tmp_path)
        expanded = await PresetCatalogService(session).expand_template(
            slug="security-ukgc-rts",
            inputs={
                "assets": ["casino"],
                "system_inputs": data["framework"]["systemInputs"],
                "period": data["framework"]["period"],
                "evidence_refs": record_refs,
                "audit_ref": audit_ref,
            },
        )
    payload = expanded["steps"][0]["tool"]["inputs"]
    direct = request_payload()
    direct["framework"].update(evidenceRefs=record_refs, auditRef=audit_ref)
    first = await invoke(service, payload)
    second = await invoke(service, direct, key="direct")
    assert first["frameworkPack"]["readiness"] == "prepared"
    assert first["publicationOutcome"] == "published"
    access = access_for_test("workflow:assessment")
    assert first["controlAssessmentRef"] == second["controlAssessmentRef"]
    report, context = await retained(service, first)
    assert context["evidenceRequests"] == []
    assert len(report["requirements"]) == 44
    assert set(statuses(report).values()) == {"evidence_recorded"}
    assert context["externalAudit"]["status"] == "Unverified"
    assert {claim["status"] for claim in context["externalClaims"].values()} == {
        "Unverified"
    }
    bundle = first["reportBundle"]
    _, text = await service.read(
        artifact_id=bundle["primary_report_ref"]["artifact_id"], access=access
    )
    assert b"Synthetic auditor" in text and b"Add test sample" in text
    assert b"customer_information" in text and b"Unverified" in text
    # Saved outputs are reused, but their inputs still require authorization.
    retried = await invoke(service, payload)
    assert retried["controlAssessmentRef"] == first["controlAssessmentRef"]
    proof = await service.verified_dependencies(
        root_artifact_id=first["controlAssessmentRef"], access=access
    )
    assert set(record_refs + [audit_ref, independence]) <= {
        d.artifact_id for d in proof.dependencies
    }


async def test_empty_audit_exports_actionable_missing_external_evidence(environment):
    result = await invoke(environment.service, request_payload())
    assert result["frameworkPack"]["readiness"] == "incomplete"
    assert result["publicationOutcome"] == "published"
    report, context = await retained(environment.service, result)
    assert len(context["evidenceRequests"]) > 44
    assert any(r["id"] == "auditor.independence" for r in context["evidenceRequests"])
    assert set(statuses(report).values()) == {"incomplete"}


async def test_clean_scan_and_uploaded_certificate_leave_audit_gaps(environment):
    service = environment.service
    payload, _, _ = await inputs(service, rules=False)
    payload["framework"] = fixture()["framework"]
    payload["framework"]["systemInputs"]["criticalSystems"] = [
        dict(row, systems=["app"])
        for row in payload["framework"]["systemInputs"]["criticalSystems"]
    ]
    payload["framework"]["systemInputs"]["thirdParties"] = []
    certificate = await put(
        service, b"Synthetic ISO certificate without control or system coverage"
    )
    payload["framework"]["evidenceRefs"] = [certificate]
    result = await invoke(service, payload)
    assert result["scanOutcome"] == "completed"
    assert sum(result["counts"].values()) == 0
    assert result["frameworkPack"]["readiness"] == "incomplete"
    _, context = await retained(service, result)
    requests = context["evidenceRequests"]
    assert any(r.get("artifactRef") == certificate for r in requests)
    assert any(r.get("requirementId") == "8.29" for r in requests)


async def test_publication_retry_preserves_prepared_artifact(environment):
    service = environment.service
    payload = request_payload()
    with patch(
        "moonmind.workflows.skills.security_assessment._summary",
        side_effect=RuntimeError("synthetic publication outage"),
    ):
        first = await invoke(service, payload)
    assert first["publicationOutcome"] == "failed"
    assert first["controlAssessmentRef"]
    retried = await invoke(service, payload)
    assert retried["publicationOutcome"] == "published"
    assert retried["controlAssessmentRef"] == first["controlAssessmentRef"]


async def test_framework_cannot_expand_admitted_scope(environment):
    from temporalio.exceptions import ApplicationError

    payload = request_payload()
    payload["scope"]["assets"] = ["different-system"]
    with pytest.raises(ApplicationError, match="ukgc-rts-security"):
        await invoke(environment.service, payload)


async def test_retained_legacy_ukgc_report_renders_exports_and_is_a_previous_assessment(
    environment,
):
    """A retained UKGC framework-audit-preparation/v1 report stays usable (#5436)."""
    from pathlib import Path

    from moonmind.workflows.skills.security_assessment import _record
    from tests.unit.security.test_assessment_evidence_exports import unpack

    service = environment.service
    legacy = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "fixtures/security/legacy_readiness_reports.json"
        ).read_text()
    )["reports"]["framework-audit-preparation/v1:ukgc-rts-security"]
    previous = await put(service, legacy)
    await _record(service, previous, [], access_for_test("workflow:assessment"))
    payload = request_payload()
    payload["framework"]["previousAssessmentRef"] = previous
    payload["evidenceExport"] = {"artifactRefs": [previous]}
    result = await invoke(service, payload)
    report, _ = await retained(service, result)
    assert report["frameworkPack"]["previousAssessmentStatus"] == "available"
    changes = report["intelligenceChanges"]
    # The edition became an identifier; the unique pack ID keeps the lineage.
    assert any("edition changed from ISO/IEC 27001:2022" in row for row in changes)
    # The retained run recorded evidence this run does not supply.
    assert any("5.1 evidence_recorded -> incomplete" in row for row in changes)
    _, readable = await service.read(
        artifact_id=recurring.report_ref(result),
        access=access_for_test("workflow:assessment"),
    )
    assert b"Previous framework-audit-preparation/v1 report" in readable
    archive, manifest = await unpack(service, result)
    entries = {row["sourceArtifactId"]: row for row in manifest["entries"]}
    assert entries[previous]["status"] == "included"
    assert previous in await recurring.required(service, result["controlAssessmentRef"])
